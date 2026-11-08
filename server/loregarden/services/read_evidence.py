"""Whether a review, verify or gate run read the diff it passed.

A reviewer's pass was taken on its word. Nothing checked that the run had
looked at the change at all, so a run that skimmed the ticket and printed
`pass` read the same as one that read every file. This answers it from the
run's own tool calls in its retained transcript, which the agent's report
cannot set:

- the Read tool and Cursor's `readToolCall` (`read_paths`), and
- shell commands that read (`shell_reads`), since reviewers mostly read
  through `git diff -- path`, `sed -n` and `grep`.

Measured before the refusal was wired, over 482 passing review, verify and gate
runs since 2026-10-20 (412 without `visual_qa`): 164 read every changed file or
the whole patch, 248 read some, and none read nothing. So the refusal is a
floor that no honest run in that window would have hit.

`visual_qa` is not judged. Its evidence is the rendered surface, not the
source, and a visual pass that opened no changed file is how it should work.

A run with no transcript (the external harness, a run that died before
writing one) is not judged either: absence of a transcript is not evidence of
not reading, and the harness's own stage path decides those runs.
"""

from __future__ import annotations

import fnmatch
import logging
import os
import re
from dataclasses import dataclass, field

from loregarden.agents.executors.read_paths import (
    CLAUDE_READ_TOOLS,
    CURSOR_READ_CALLS,
    CursorCallBody,
    ReadArgs,
    TranscriptEvent,
)
from loregarden.agents.executors.shell_reads import ShellReads, shell_reads
from loregarden.models.domain import (
    AgentRun,
    Artifact,
    ArtifactKind,
    OrchestratorDecision,
    ReadEvidenceStatus,
    Ticket,
)
from loregarden.services.orchestrator_decisions import record_orchestrator_decision
from pydantic import BaseModel, Field, ValidationError
from sqlmodel import Session, col, select

logger = logging.getLogger(__name__)

#: The lanes whose pass rests on having read the change.
CHECKED_AGENTS = frozenset(
    {
        "architecture_reviewer",
        "static_qa",
        "security_reviewer",
        "gdscript_reviewer",
        "verifier",
        "gatekeeper",
        "ac_gatekeeper",
    }
)

#: Claude's shell tool, and the wrapper Cursor nests its shell call under.
CLAUDE_SHELL_TOOL = "Bash"
CURSOR_SHELL_CALL = "shellToolCall"

_WORKTREE_PREFIX = re.compile(r"/(?:\.claude/)?\.?worktrees/[^/]+/(.*)")


class _ShellArgs(BaseModel):
    command: str = ""


class _DiffSection(BaseModel):
    path: str = ""


class _DiffContent(BaseModel):
    sections: list[_DiffSection] = Field(default_factory=list)


@dataclass
class TranscriptReads:
    """Every read a transcript shows, before it is compared with any diff."""

    paths: set[str] = field(default_factory=set)
    pathspecs: set[str] = field(default_factory=set)
    whole_diff: bool = False
    tool_calls: int = 0
    #: Lines that parsed as transcript events. Zero means there is no
    #: transcript to judge (a harness that writes plain text), not "read nothing".
    events: int = 0

    def add_shell(self, reads: ShellReads) -> None:
        self.paths |= reads.named
        self.pathspecs |= reads.pathspecs
        self.whole_diff = self.whole_diff or reads.whole_diff


@dataclass(frozen=True)
class ReadEvidence:
    """How much of the diff one run read."""

    status: ReadEvidenceStatus
    run_id: str
    run_code: str
    read: tuple[str, ...]
    diff_count: int
    tool_calls: int
    whole_diff: bool

    def summary(self) -> str:
        """One sentence for the history rail and the agent sent back."""
        who = f"Run {self.run_code}"
        match self.status:
            case ReadEvidenceStatus.NO_TOOLS:
                return f"{who} made no tool calls, so it read nothing of the diff."
            case ReadEvidenceStatus.NONE:
                return (
                    f"{who} read nothing of the diff: none of its {self.tool_calls} "
                    f"tool calls read any of the {self.diff_count} changed files."
                )
            case ReadEvidenceStatus.OK if self.whole_diff:
                return f"{who} read the whole diff ({self.diff_count} files)."
            case _:
                return f"{who} read {len(self.read)} of the {self.diff_count} changed files."

    def as_evidence(self) -> dict[str, object]:
        return {
            "read_evidence": self.status.value,
            "run_code": self.run_code,
            "files_read": list(self.read),
            "diff_files": self.diff_count,
            "tool_calls": self.tool_calls,
        }


def _args_paths(raw: object) -> set[str]:
    try:
        return set(ReadArgs.model_validate(raw).paths())
    except ValidationError:
        logger.warning("ignoring an unparseable read tool call in a transcript", exc_info=True)
        return set()


def _shell_command(raw: object) -> str:
    try:
        return _ShellArgs.model_validate(raw).command
    except ValidationError:
        logger.warning("ignoring an unparseable shell tool call in a transcript", exc_info=True)
        return ""


def _add_claude_event(reads: TranscriptReads, event: TranscriptEvent) -> None:
    if event.type != "assistant" or event.message is None:
        return
    for block in event.message.content:
        if block.type != "tool_use":
            continue
        reads.tool_calls += 1
        if block.name in CLAUDE_READ_TOOLS:
            reads.paths |= _args_paths(block.input)
        elif block.name == CLAUDE_SHELL_TOOL:
            reads.add_shell(shell_reads(_shell_command(block.input)))


def _cursor_args(body: object) -> dict[str, object]:
    try:
        return CursorCallBody.model_validate(body).args
    except ValidationError:
        logger.warning("ignoring an unparseable cursor tool call in a transcript", exc_info=True)
        return {}


def _add_cursor_event(reads: TranscriptReads, event: TranscriptEvent) -> None:
    if event.type != "tool_call" or event.subtype != "started":
        return
    reads.tool_calls += 1
    for name, body in event.tool_call.items():
        if name in CURSOR_READ_CALLS:
            reads.paths |= _args_paths(_cursor_args(body))
        elif name == CURSOR_SHELL_CALL:
            reads.add_shell(shell_reads(_shell_command(_cursor_args(body))))


def transcript_reads(stdout: str) -> TranscriptReads:
    """Every read in a transcript, in either the Claude or the Cursor schema.

    Unparseable lines are skipped, as `read_paths` does: a transcript cut off
    mid-line by a kill should not lose every read before the cut.
    """
    reads = TranscriptReads()
    for line in stdout.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            event = TranscriptEvent.model_validate_json(line)
        except ValidationError:
            continue
        if not event.type:
            # Any JSON object validates (every field defaults), including the
            # stage report's own payload line; only a typed line is an event.
            continue
        reads.events += 1
        _add_claude_event(reads, event)
        _add_cursor_event(reads, event)
    return reads


def _repo_relative(raw: str, repo_root: str) -> str:
    """`raw` relative to the repo, matched lexically; a worktree prefix is dropped.

    A run's paths are absolute in whichever checkout it ran in, and that is not
    always `start_repo_path` (a worktree under the primary checkout), so a path
    outside the root falls back to whatever follows a worktree directory.
    """
    path = os.path.normpath(raw) if raw else raw
    if repo_root and os.path.isabs(path):
        root = os.path.normpath(repo_root)
        if path == root or path.startswith(root + os.sep):
            return os.path.relpath(path, root)
    found = _WORKTREE_PREFIX.search(path)
    if found:
        return found.group(1)
    return path.removeprefix("./").rstrip("/")


def _read_any(diff_path: str, read: set[str]) -> bool:
    # Suffix matches either way: a path named from a directory the cwd tracking
    # missed (`sed -n 1p loregarden/x.py` inside `server/`), or an absolute path
    # in a checkout with no recognisable worktree prefix. Generous on purpose;
    # see `shell_reads` for why a false read is the cheap mistake.
    return any(
        diff_path == path or path.endswith("/" + diff_path) or diff_path.endswith("/" + path)
        for path in read
    )


def _selected_by(diff_path: str, pathspecs: set[str]) -> bool:
    return any(
        diff_path == spec or diff_path.startswith(spec + "/") or fnmatch.fnmatch(diff_path, spec)
        for spec in pathspecs
    )


def diff_files(session: Session, ticket: Ticket) -> tuple[str, ...]:
    """The changed files of the ticket's latest captured diff, or () if none."""
    artifact = session.exec(
        select(Artifact)
        .where(Artifact.ticket_id == ticket.id, Artifact.kind == ArtifactKind.DIFF)
        .order_by(col(Artifact.created_at).desc())
    ).first()
    if artifact is None:
        return ()
    try:
        content = _DiffContent.model_validate_json(artifact.content_json or "{}")
    except ValidationError:
        # Judging against an empty diff would refuse nothing, silently.
        logger.warning("diff artifact %s is unreadable; read evidence not judged", artifact.id)
        return ()
    return tuple(sorted({section.path for section in content.sections if section.path}))


def judge(
    stdout: str, repo_root: str, changed: tuple[str, ...]
) -> tuple[ReadEvidenceStatus, tuple[str, ...], int, bool] | None:
    """The status, the changed files read, the tool-call count, and whole-diff.

    None when `stdout` holds no transcript events at all.
    """
    reads = transcript_reads(stdout)
    if reads.events == 0:
        return None
    read = {_repo_relative(path, repo_root) for path in reads.paths}
    specs = {_repo_relative(spec, repo_root) for spec in reads.pathspecs}
    hit = tuple(path for path in changed if _read_any(path, read) or _selected_by(path, specs))
    if reads.whole_diff or len(hit) == len(changed):
        status = ReadEvidenceStatus.OK
    elif hit:
        status = ReadEvidenceStatus.INSUFFICIENT
    elif reads.tool_calls == 0:
        status = ReadEvidenceStatus.NO_TOOLS
    else:
        status = ReadEvidenceStatus.NONE
    return status, hit, reads.tool_calls, reads.whole_diff


def latest_stage_run(
    session: Session, ticket: Ticket, *, agent_id: str, stage_key: str
) -> AgentRun | None:
    return session.exec(
        select(AgentRun)
        .where(
            AgentRun.ticket_id == ticket.id,
            AgentRun.agent_id == agent_id,
            AgentRun.stage_key == stage_key,
        )
        .order_by(col(AgentRun.created_at).desc())
    ).first()


def run_read_evidence(session: Session, ticket: Ticket, run: AgentRun) -> ReadEvidence | None:
    """How much of the diff `run` read, or None when it is not judged.

    Not judged: a lane outside `CHECKED_AGENTS`, a run with no transcript (no
    stdout, or none of it parses as a transcript event), or a ticket with no
    captured diff to read.
    """
    if run.agent_id not in CHECKED_AGENTS or not (run.stdout or "").strip():
        return None
    changed = diff_files(session, ticket)
    if not changed:
        return None
    judged = judge(run.stdout, run.start_repo_path, changed)
    if judged is None:
        return None
    status, hit, tool_calls, whole = judged
    return ReadEvidence(
        status=status,
        run_id=run.id,
        run_code=run.run_code,
        read=hit,
        diff_count=len(changed),
        tool_calls=tool_calls,
        whole_diff=whole,
    )


def stage_read_evidence(
    session: Session, ticket: Ticket, *, agent_id: str, stage_key: str
) -> ReadEvidence | None:
    """`run_read_evidence` for the newest run of this agent at this stage."""
    if agent_id not in CHECKED_AGENTS:
        return None
    run = latest_stage_run(session, ticket, agent_id=agent_id, stage_key=stage_key)
    return run_read_evidence(session, ticket, run) if run is not None else None


def record_read_evidence(session: Session, ticket: Ticket, run: AgentRun) -> None:
    """Put a passing run's read evidence on the ticket's history rail.

    Called once per finished run, beside the verdict that acts on it, so the
    rail says what the run read whether the pass stood or was refused.
    """
    evidence = run_read_evidence(session, ticket, run)
    if evidence is None:
        return
    refused = evidence.status in (ReadEvidenceStatus.NONE, ReadEvidenceStatus.NO_TOOLS)
    record_orchestrator_decision(
        session,
        ticket,
        decision=(
            OrchestratorDecision.REFUSED_UNREAD_PASS
            if refused
            else OrchestratorDecision.READ_EVIDENCE_CHECKED
        ),
        stage_key=run.stage_key,
        reason=evidence.summary(),
        run_id=run.id,
        evidence=evidence.as_evidence(),
    )
