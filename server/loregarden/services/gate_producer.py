"""Which agent run produced the work a transition gate judged, and on what tree.

`GateEvaluated.orchestration_run_id` is an `orchestration_runs.id` — the gate is
evaluated by the orchestrator between stages, not by an agent, so it was never an
agent run id and never joined `/api/runs`. The agent run is one hop away:
`agent_runs.orchestration_run_id` names the same orchestration, and its
`stage_key` names the stage whose output the gate is checking (`from_stage`).

Two uses, one rule:

- **Recording.** `gate_producer_payload` is merged into every new
  `GateEvaluated` payload: the producing run's id, agent, adapter and model,
  plus the worktree's `head_sha` and a `tree_sha` of exactly what the gate saw
  (uncommitted and untracked files included — gates run before the stage's work
  is committed).
- **History.** `pick_producer` applies the same rule to rows written before this
  existed, and says which join it used, because the two are not equally good.

Records what ran. It does not decide what should run: adapter resolution lives
in `agents.cli_adapters` and is deliberately untouched here.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import tempfile
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from pathlib import Path, PurePath

from loregarden.core.timestamps import as_utc
from loregarden.models.domain import AgentRun, CliAdapter, OrchestrationRun, Ticket, Workspace
from loregarden.services.gate_eval_classify import canonical_stage
from loregarden.services.git_subprocess import run_git
from loregarden.services.ticket_worktree import resolve_ticket_root
from pydantic import BaseModel
from sqlmodel import Session, col, select

logger = logging.getLogger(__name__)

_GIT_TIMEOUT_SECONDS = 30

#: argv[0] basenames of the CLIs `agents.cli_adapters` launches. A binary
#: overridden through `LOREGARDEN_*_BIN` to a wrapper with another name reads as
#: unknown — the honest answer, since nothing else on the run says.
_ADAPTER_BINARIES = {
    "claude": CliAdapter.CLAUDE,
    "cursor-agent": CliAdapter.CURSOR,
    "codex": CliAdapter.CODEX,
    "opencode": CliAdapter.OPENCODE,
}
#: The in-process runners launch `python -m <module>`.
_ADAPTER_MODULES = {
    "loregarden.agents.executors.local_runner": CliAdapter.LOCAL,
    "loregarden.agents.executors.lmstudio_runner": CliAdapter.LMSTUDIO,
}
_TERMINAL_HANDOFF_PREFIX = "[terminal-handoff] "


class ProducerJoin(StrEnum):
    """How a gate event was tied to the agent run that produced its input."""

    #: Written on the event at evaluation time.
    RECORDED = "recorded"
    #: Historical: same orchestration run and stage, latest finished before the gate.
    ORCHESTRATION_RUN = "orchestration_run"
    #: Historical fallback: same ticket and stage, latest finished before the gate.
    #: Weaker — a requeue or a manual run between the two lands here too.
    TICKET = "ticket"


class GateProducer(BaseModel):
    agent_run_id: str
    agent_id: str
    adapter: CliAdapter | None
    model: str | None
    join: ProducerJoin


def adapter_from_command(command: str) -> CliAdapter | None:
    """The adapter an agent run used, read from the argv it recorded.

    `agent_runs` stores the command line but not the adapter, so this is the
    only record of what actually ran — which is the point: the workspace's
    `cli_adapter` can override an agent's configured one.
    """
    tokens = command.removeprefix(_TERMINAL_HANDOFF_PREFIX).split()
    if not tokens:
        return None
    by_binary = _ADAPTER_BINARIES.get(PurePath(tokens[0]).name)
    if by_binary is not None:
        return by_binary
    return next((_ADAPTER_MODULES[t] for t in tokens[1:3] if t in _ADAPTER_MODULES), None)


def pick_producer(
    runs: Iterable[AgentRun],
    *,
    orchestration_run_id: str | None,
    stage_key: str,
    before: datetime,
) -> GateProducer | None:
    """The latest run of *stage_key* that finished before *before*.

    Prefers a run of the same orchestration; falls back to any run of the
    ticket. *runs* must all belong to the gate's ticket. A fanned-out stage has
    several runs and the latest one wins, which names one producer of several.
    """
    stage = canonical_stage(stage_key)
    cutoff = as_utc(before)
    finished = sorted(
        (
            r
            for r in runs
            if r.finished_at is not None
            and canonical_stage(r.stage_key) == stage
            and as_utc(r.finished_at) <= cutoff
        ),
        key=lambda r: (as_utc(r.finished_at), r.id),
        reverse=True,
    )
    same_orch = next(
        (
            r
            for r in finished
            if orchestration_run_id and r.orchestration_run_id == orchestration_run_id
        ),
        None,
    )
    if same_orch is not None:
        return _producer(same_orch, ProducerJoin.ORCHESTRATION_RUN)
    if finished:
        return _producer(finished[0], ProducerJoin.TICKET)
    return None


def _producer(run: AgentRun, join: ProducerJoin) -> GateProducer:
    return GateProducer(
        agent_run_id=run.id,
        agent_id=run.agent_id,
        adapter=adapter_from_command(run.command),
        model=run.model,
        join=join,
    )


@dataclass(frozen=True)
class WorktreeSnapshot:
    head_sha: str | None = None
    #: A git tree of the working tree as the gate saw it, tracked or not
    #: (`.gitignore` respected). Unreferenced, so `git gc` may prune it after
    #: `gc.pruneExpire` — pin it before relying on it long-term.
    tree_sha: str | None = None
    error: str = ""


def snapshot_worktree(repo_root: Path) -> WorktreeSnapshot:
    """HEAD and a tree of the uncommitted state, without touching the real index.

    Copies the index to a scratch file and runs `add -A` + `write-tree` against
    the copy, so only changed files are hashed and neither the index nor any ref
    moves. A failure comes back on the snapshot, not as an exception: a gate
    must still be recorded when its tree cannot be.
    """
    head = _git(["rev-parse", "HEAD"], repo_root)
    if head.returncode != 0:
        return WorktreeSnapshot(error=f"rev-parse HEAD: {head.stderr.strip()}")
    head_sha = head.stdout.strip()
    index = _git(["rev-parse", "--git-path", "index"], repo_root)
    if index.returncode != 0:
        return WorktreeSnapshot(head_sha, error=f"rev-parse index: {index.stderr.strip()}")
    real_index = repo_root / index.stdout.strip()
    with tempfile.TemporaryDirectory(prefix="loregarden-gate-snapshot-") as tmp:
        scratch = Path(tmp) / "index"
        if real_index.is_file():
            shutil.copyfile(real_index, scratch)
        added = _git(["add", "-A"], repo_root, index_file=scratch)
        if added.returncode != 0:
            return WorktreeSnapshot(head_sha, error=f"add -A: {added.stderr.strip()}")
        tree = _git(["write-tree"], repo_root, index_file=scratch)
        if tree.returncode != 0:
            return WorktreeSnapshot(head_sha, error=f"write-tree: {tree.stderr.strip()}")
    return WorktreeSnapshot(head_sha, tree.stdout.strip())


def _git(args: list[str], cwd: Path, index_file: Path | None = None) -> subprocess.CompletedProcess:
    return run_git(
        args,
        cwd=cwd,
        index_file=index_file,
        capture_output=True,
        text=True,
        timeout=_GIT_TIMEOUT_SECONDS,
    )


def gate_producer_payload(
    session: Session,
    ticket: Ticket,
    orch_run: OrchestrationRun | None,
    *,
    from_stage: str,
    evaluated_at: datetime,
) -> dict:
    """The attribution fields merged into a new `GateEvaluated` payload.

    Every key is always present, `None` when unknown, so a reader can tell "not
    recorded" (key absent — an event from before this) from "recorded, nothing
    found". A snapshot failure is logged and carried in `snapshot_error`.
    """
    runs = session.exec(select(AgentRun).where(col(AgentRun.ticket_id) == ticket.id)).all()
    producer = pick_producer(
        runs,
        orchestration_run_id=orch_run.id if orch_run else None,
        stage_key=from_stage,
        before=evaluated_at,
    )
    snapshot = _snapshot_for(session, ticket)
    return {
        "agent_run_id": producer.agent_run_id if producer else None,
        "agent_id": producer.agent_id if producer else None,
        "adapter": producer.adapter.value if producer and producer.adapter else None,
        "model": producer.model if producer else None,
        "head_sha": snapshot.head_sha,
        "tree_sha": snapshot.tree_sha,
        "snapshot_error": snapshot.error or None,
    }


def _snapshot_for(session: Session, ticket: Ticket) -> WorktreeSnapshot:
    workspace = session.get(Workspace, ticket.workspace_id)
    if workspace is None:
        return WorktreeSnapshot(error="ticket has no workspace")
    repo_root = resolve_ticket_root(session, ticket, workspace)
    try:
        snapshot = snapshot_worktree(repo_root)
    except (OSError, subprocess.TimeoutExpired) as exc:
        snapshot = WorktreeSnapshot(error=f"{type(exc).__name__}: {exc}")
    if snapshot.error:
        logger.warning(
            "gate snapshot for ticket %s at %s failed: %s", ticket.id, repo_root, snapshot.error
        )
    return snapshot
