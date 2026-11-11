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
from pydantic import BaseModel, ConfigDict, Field, ValidationError

logger = logging.getLogger(__name__)

TOOL_USE_BLOCK = "tool_use"  # py-org: allow-string - Claude stream-json vocabulary

#: The first word of a call line, the way `result` is the first of a result line.
CALL_HEAD = "call"

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
