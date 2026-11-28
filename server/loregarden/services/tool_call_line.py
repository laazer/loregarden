"""One log line per tool call a Claude run makes: which tool, and on what.

The Claude adapter logged a tool's *result* and never the call, so a run's log
said what came back and not what was asked. Measured on the live database
before this was written: 71,838 TOOL rows, of which the Claude-schema ones were
all `result` bodies or permission notes — not one named the tool that ran in
an auto-approved run. Hive's station map needs the name; so does anyone reading
the log.

The line is `call · <tool>` with an optional ` · <subject>`: a name, never the
content. A file is its last two path parts, a URL its host, a shell command
its leading words and the description Claude wrote for it. The result line
that follows carries the content, as it always has.
"""

from __future__ import annotations

import logging
import re
import shlex
from urllib.parse import urlsplit

from loregarden.dot_line import TOOL, LogLine, clip
from loregarden.models.domain.enums import CliAdapter
from pydantic import BaseModel, ConfigDict, Field, ValidationError

logger = logging.getLogger(__name__)

TOOL_USE_BLOCK = "tool_use"  # py-org: allow-string - Claude stream-json vocabulary
TOOL_RESULT_BLOCK = "tool_result"  # py-org: allow-string - Claude stream-json vocabulary

#: The first word of a call line, the way `result` is the first of a result line.
CALL_HEAD = "call"

#: The first word of a result line. Every result line starts here, failed or not.
RESULT_HEAD = "result"

#: The suffix that marks a result line as a failed call: `result · failed`. The Hive map's
#: `classifyLine` keys on that exact rendered headline and reads every other `result · <suffix>`
#: as a clean result, so this is a shared vocabulary and not a private label — renaming it
#: without the client is the bug the constant exists to make visible.
RESULT_FAILED = "failed"

#: The adapters whose streams this module can read a call out of. Only the Claude adapter sends
#: `tool_use` blocks, so only a Claude run is *expected* to carry `call ·` lines; a run on any
#: other adapter with none is unknown activity, not an idle one (lg-hive-office-2784, S7).
#: It lives here, beside the writer, because the writer is what makes the prediction true.
CALL_LINE_ADAPTERS = frozenset({CliAdapter.CLAUDE})

SUBJECT_CHARS = 80

#: Command words whose subcommand decides what the command does (`git status`
#: reads files, `git push` goes to the network), so the subject keeps both.
_STATEMENT_BREAK = re.compile(r"&&|\|\||;|\n")

_SUBCOMMAND_HEADS = frozenset({"git", "gh", "npm", "npx", "pnpm", "uv", "task", "docker", "yarn"})


class ToolInput(BaseModel):
    """The few arguments a subject is read from; every other key is ignored."""

    model_config = ConfigDict(extra="ignore")

    file_path: str | None = None
    notebook_path: str | None = None
    path: str | None = None
    pattern: str | None = None
    command: str | None = None
    description: str | None = None
    url: str | None = None
    query: str | None = None
    skill: str | None = None
    subagent_type: str | None = None


class ToolUseBlock(BaseModel):
    """A content block of an assistant message; only `tool_use` ones are calls."""

    model_config = ConfigDict(extra="ignore")

    type: str = ""
    name: str = ""
    input: dict[str, object] = Field(default_factory=dict)


def _last_two(path: str) -> str:
    parts = [part for part in path.replace("\\", "/").split("/") if part]
    return "/".join(parts[-2:])


def _host(url: str) -> str:
    return urlsplit(url).hostname or ""


def command_head(command: str) -> str:
    """The words that say what a shell command is: `git push`, `pytest`, `ls`.

    Skips `cd …` statements and `VAR=value` prefixes, which say where and how a
    command runs, not what it is. Statements end at `&&`, `||`, `;` or a newline:
    over the last 150 Claude runs, 464 of 3,834 Bash calls were multi-line
    scripts opening with a bare `cd <worktree>` line. An unparseable
    statement (an open quote) falls back to whitespace splitting rather than to
    nothing.
    """
    for segment in _STATEMENT_BREAK.split(command):
        try:
            words = shlex.split(segment)
        except ValueError:
            words = segment.split()
        words = [word for word in words if "=" not in word.split("/")[0]]
        if not words or words[0] == "cd":
            continue
        head = words[0].rsplit("/", 1)[-1]
        if head in _SUBCOMMAND_HEADS and len(words) > 1:
            return f"{head} {words[1]}"
        return head
    return ""


def _shell_subject(args: ToolInput) -> str:
    head = command_head(args.command or "")
    description = (args.description or "").strip()
    if head and description:
        return f"{head}: {description}"
    return head or description


def _subject(name: str, args: ToolInput) -> str:
    """What the call acted on, by name only."""
    if name == "Bash":
        return _shell_subject(args)
    path = args.file_path or args.notebook_path
    if path:
        return _last_two(path)
    if args.url:
        return _host(args.url)
    for value in (args.description, args.skill, args.pattern, args.query, args.path):
        if value:
            return value.strip()
    return ""


def tool_call_line(block: ToolUseBlock) -> LogLine:
    try:
        args = ToolInput.model_validate(block.input)
    except ValidationError:
        # A non-string argument (a list `path`) has no subject worth guessing;
        # the tool's name is still the line's point, so it is kept.
        logger.warning("tool call %s has unreadable arguments", block.name, exc_info=True)
        args = ToolInput()
    subject = " ".join(_subject(block.name, args).split())
    return TOOL / CALL_HEAD / clip(block.name, 200, fallback="tool") / clip(subject, SUBJECT_CHARS)


def tool_call_lines(content: object) -> list[LogLine]:
    """A call line for each `tool_use` block in an assistant message's content."""
    if not isinstance(content, list):  # py-org: allow-isinstance - third-party CLI payload
        return []
    lines: list[LogLine] = []
    for raw in content:
        try:
            block = ToolUseBlock.model_validate(raw)
        except ValidationError:
            continue  # silent-ok: a non-object block carries no call; the text/thinking readers handle the rest
        if block.type == TOOL_USE_BLOCK:
            lines.append(tool_call_line(block))
    return lines


def _result_text(content: object) -> str:
    """The text a tool handed back, from either shape the CLIs send it in."""
    if isinstance(content, str):  # py-org: allow-isinstance - third-party CLI payload
        return content
    if not isinstance(content, list):  # py-org: allow-isinstance - third-party CLI payload
        return ""
    parts: list[str] = []
    for block in content:
        if not isinstance(block, dict):  # py-org: allow-isinstance - third-party CLI payload
            continue
        text = block.get("text") or block.get("tool_name")
        if text:
            parts.append(str(text))
    return "\n".join(parts)


def tool_result_line(blocks: object) -> LogLine | None:
    """The one result line a `user` event's `tool_result` blocks write.

    `is_error` used to be read by nobody: the formatter kept the block's `content` and dropped
    the flag, so a failed call and a clean one produced the byte-identical `result` line and no
    reader could tell them apart (lg-hive-office-2784, S1a).

    The headline is `result · failed` when **any** block in the event carries `is_error` exactly
    `True`, and `result` otherwise. The log keeps one line per event, so a multi-block event with
    one error is one failed line: a partial success reading as success would hide the failure,
    which is the whole point of recording it.

    Only the boolean `True` is a failure. 6 of the 16 `tool_result` blocks in the recorded payload
    `data/run-logs/run_75d1f3-2cd98e21.out` carry no `is_error` key at all, so an `is not False`
    test would call every one of them failed; a truthiness test would do the same to the string
    `"false"`. The body is untouched — the word "Error" inside a result is ordinary content (22,147
    live result bodies carry it), and S4 rejects reading failure out of it.

    `None` when the blocks carry no text, which is today's behaviour for an empty result and the
    reason the marker cannot smuggle a bodiless line into the log.
    """
    if not isinstance(blocks, list):  # py-org: allow-isinstance - third-party CLI payload
        return None
    texts: list[str] = []
    failed = False
    for block in blocks:
        if not isinstance(block, dict):  # py-org: allow-isinstance - third-party CLI payload
            continue
        if block.get("type") != TOOL_RESULT_BLOCK:
            continue
        texts.append(_result_text(block.get("content")))
        failed = failed or block.get("is_error") is True
    body = "\n".join(text for text in texts if text.strip())
    if not body.strip():
        return None
    headline = TOOL / RESULT_HEAD / (RESULT_FAILED if failed else "")
    return headline.with_body(body)
