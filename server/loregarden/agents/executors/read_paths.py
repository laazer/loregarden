"""What files a run READ, recovered from the CLI transcript.

`changed_paths_json` answers what a run wrote. Nothing answered what it read, and
a reviewer writes nothing — so every review run looked identical from the
outside. `lg-workflow-integrity-499` needs the difference: a lens that passed and
whose subject the rework did not touch has nothing to re-derive.

The reads were never missing, only unlooked-for. They are in the retained
transcript, in two schemas, and this module reads both:

  Claude   {"type":"assistant","message":{"content":[{"type":"tool_use",
            "name":"Read","input":{"file_path":"..."}}]}}
  Cursor   {"type":"tool_call","tool_call":{"readToolCall":{"args":{...}}}}

Measured before this was written, because a signal nobody has read from a real
run is a guess: of 60 Claude-schema runs, 58 used Read and all 58 yielded paths
(462 distinct, 526 Read calls); 6 cursor runs yielded 190 readToolCall events.

Its own module rather than a method on `CliAgentExecutor`, which is at the
1000-line class cap that already forced `prompt_size` out.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

from loregarden.models.domain import AgentRun
from pydantic import BaseModel, Field, ValidationError
from sqlmodel import Session

logger = logging.getLogger(__name__)

#: Tools whose call means "this run looked at the contents of this file".
#:
#: Deliberately NOT Grep or Glob. Those name a search scope — a directory, a
#: pattern — not a file whose contents were examined, and folding them in would
#: inflate every run's list with whole trees. They are a different signal and
#: 499 can ask for them separately if intersecting on read files proves too
#: narrow.
CLAUDE_READ_TOOLS = frozenset({"Read", "NotebookRead"})

#: The cursor-agent equivalents, keyed by the wrapper name the schema uses.
CURSOR_READ_CALLS = frozenset({"readToolCall"})

#: Argument keys that carry a path in either schema.
PATH_KEYS = ("file_path", "notebook_path", "path")

#: Upper bound on stored paths. Observed usage is 17-43 per run, so this is far
#: above real traffic and exists to stop a pathological run writing an unbounded
#: blob. A list AT the cap must be read as possibly incomplete: for 499 that
#: means re-running the lens rather than assuming the missing paths were not
#: touched.
MAX_READ_PATHS = 1000


class ReadArgs(BaseModel):
    """The path-bearing arguments of a read call, in either schema.

    A model rather than dictionary probing: the transcript is a foreign payload,
    and `isinstance(args.get("path"), str)` is a schema check written by hand.
    Extra keys (`limit`, `toolCallId`) are ignored; a path that is not a string
    fails validation and the call is skipped rather than stringified into a
    plausible-looking wrong answer.
    """

    file_path: str | None = None
    notebook_path: str | None = None
    path: str | None = None

    def paths(self) -> list[str]:
        return [value for value in (self.file_path, self.notebook_path, self.path) if value]


class CursorCallBody(BaseModel):
    """The cursor schema nests the arguments one level deeper than Claude."""

    args: dict[str, object] = Field(default_factory=dict)


class ContentBlock(BaseModel):
    """One block of an assistant message. Claude puts tool calls here."""

    type: str = ""
    name: str = ""
    input: dict[str, object] = Field(default_factory=dict)


class TranscriptMessage(BaseModel):
    content: list[ContentBlock] = Field(default_factory=list)


class TranscriptEvent(BaseModel):
    """One line of the transcript, in whichever schema the adapter writes.

    `message.content` is a list of blocks on assistant events and a bare string
    on user events. The string form fails validation, which is the intended
    outcome: a user event carries no tool call, so skipping it loses nothing.
    """

    type: str = ""
    message: TranscriptMessage | None = None
    tool_call: dict[str, object] = Field(default_factory=dict)


def _args_paths(raw: object) -> list[str]:
    try:
        return ReadArgs.model_validate(raw).paths()
    except ValidationError:
        return []


def _paths_from_claude_event(event: TranscriptEvent) -> list[str]:
    if event.message is None:
        return []
    found: list[str] = []
    for block in event.message.content:
        if block.type == "tool_use" and block.name in CLAUDE_READ_TOOLS:
            found.extend(_args_paths(block.input))
    return found


def _paths_from_cursor_event(event: TranscriptEvent) -> list[str]:
    if event.type != "tool_call":
        return []
    found: list[str] = []
    for name in CURSOR_READ_CALLS:
        body = event.tool_call.get(name)
        if body is None:
            continue
        try:
            args = CursorCallBody.model_validate(body).args
        except ValidationError:
            continue
        found.extend(_args_paths(args))
    return found


def _resolved(path: Path) -> Path | None:
    try:
        return path.resolve()
    except (OSError, ValueError, RuntimeError):
        return None


def _relative_to_repo(raw: str, repo_root: Path) -> str | None:
    """The repo-relative form of a read path, or None if it is not in the repo.

    A run reads plenty that is not the workspace — `~/.cursor/...`, skill files,
    its own agent-tools scratch. Those say nothing about whether a rework touched
    what a lens examined, and keeping them would make every intersection true.

    Matched LEXICALLY first, and that is not a detail. A repo directory can be a
    symlink pointing outside the tree — `agent_context` in some workspaces is a
    symlink into iCloud — and `Path.resolve()` follows it, so a file the run read
    at `<repo>/agent_context/...` resolves somewhere else entirely and is dropped
    as foreign. Measuring the parser against real transcripts is what surfaced
    that: three runs showed `readToolCall` events and yielded nothing.

    The resolved comparison is kept as a fallback, because the other direction is
    also real: a repo root reached through a symlink (`/tmp` vs `/private/tmp` on
    macOS) needs resolution to match at all. `..` segments are collapsed before
    either comparison, so a path climbing out of the tree still fails both.
    """
    candidate = Path(raw)
    if not candidate.is_absolute():
        candidate = repo_root / candidate

    targets = [Path(os.path.normpath(candidate))]
    resolved_target = _resolved(candidate)
    if resolved_target is not None:
        targets.append(resolved_target)

    bases = [Path(os.path.normpath(repo_root))]
    resolved_base = _resolved(repo_root)
    if resolved_base is not None:
        bases.append(resolved_base)

    for base in bases:
        for target in targets:
            try:
                return target.relative_to(base).as_posix()
            except ValueError:
                continue
    return None


def extract_read_paths(stdout: str, repo_root: Path) -> list[str]:
    """Repo-relative files this transcript shows the run reading, sorted.

    Unparseable lines are skipped rather than failing the whole extraction: a
    transcript is a stream that can be cut off mid-line when a run is killed, and
    losing every read because the last line is half-written would be worse than
    losing that line.
    """
    seen: set[str] = set()
    for line in stdout.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            event = TranscriptEvent.model_validate_json(line)
        except ValidationError:
            continue
        for raw in _paths_from_claude_event(event) + _paths_from_cursor_event(event):
            relative = _relative_to_repo(raw, repo_root)
            if relative:
                seen.add(relative)
    paths = sorted(seen)
    if len(paths) > MAX_READ_PATHS:
        logger.warning(
            "run read %d files, above the %d cap; stored list is truncated",
            len(paths),
            MAX_READ_PATHS,
        )
        paths = paths[:MAX_READ_PATHS]
    return paths


def record_read_paths(session: Session, run: AgentRun, stdout: str, repo_root: Path) -> None:
    """Store what this run read, and stamp when the recorder ran.

    The stamp is written whatever the outcome, including an empty list, so that
    "read nothing in the repo" is distinguishable from "nobody looked" — the
    distinction `changed_paths_json` had to add later, having meant three things
    at once for 1086 rows (lg-workflow-integrity-675).
    """
    run.read_paths_json = json.dumps(extract_read_paths(stdout, repo_root))
    run.read_paths_recorded_at = datetime.now(timezone.utc)
    session.add(run)
    session.commit()
