"""Commit the files land would refuse after a writing stage has passed.

Landing merges commits only. A pipeline that grades the worktree and then
lands without committing produces the dirty ticket worktree land refuses.
This helper is the orchestrator's commit of that path set — not `git add -A`,
not a push, not a PR, and not something an agent is allowed to do.

Callers map the result onto their surface. Skip and success continue; `error`
is failure. The helper does not know about stages, reports, or orchestration.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from loregarden.models.domain import (
    AgentRun,
    RunStatus,
    Ticket,
    Worktree,
    WorktreeState,
)
from loregarden.services.git_automation_config import resolve_git_automation
from loregarden.services.git_commit_push_service import commit_paths_in
from loregarden.services.landing_uncommitted import uncommitted_at_landing
from loregarden.services.ticket_workspace_binding import workspace_for_ticket
from loregarden.services.ticket_worktree import resolve_ticket_root
from loregarden.services.workspace_paths import resolve_workspace_root
from sqlmodel import Session, select

#: Statuses that may offer a commit. A still-RUNNING triage run is passed in so
#: live_writer does not match it (R4); every other non-SUCCEEDED status is
#: failed_run.
_COMMITTABLE = frozenset({RunStatus.SUCCEEDED, RunStatus.RUNNING})


class WorktreeCommitSkip(StrEnum):
    """Why the helper did not commit. Result field, not a DB vocabulary."""

    COMMIT_OFF = "commit_off"
    NOT_TICKET_WORKTREE = "not_ticket_worktree"
    FAILED_RUN = "failed_run"
    CLEAN = "clean"
    LIVE_WRITER = "live_writer"
    NONE = "none"


@dataclass(frozen=True)
class RunWorktreeCommit:
    """What `commit_succeeded_run_worktree` did, or why it stopped."""

    attempted: bool
    committed: bool
    error: str = ""
    paths: tuple[str, ...] = ()
    skipped: WorktreeCommitSkip = WorktreeCommitSkip.NONE


def commit_succeeded_run_worktree(
    session: Session, ticket: Ticket, run: AgentRun | None = None
) -> RunWorktreeCommit:
    """Commit land's path set on this ticket's worktree, or explain the skip.

    Evaluation stops at the first match. Callers treat `error` as failure;
    skip and success are the pass path.
    """
    skip = _skip_reason(session, ticket, run)
    if skip is not None:
        return _skip(skip)

    workspace = workspace_for_ticket(session, ticket)
    if workspace is None:
        return _skip(WorktreeCommitSkip.NOT_TICKET_WORKTREE)

    ticket_root = resolve_ticket_root(session, ticket, workspace)
    try:
        paths = uncommitted_at_landing(session, ticket, workspace)
    except RuntimeError as exc:
        return _fail(str(exc))

    if not paths:
        return _skip(WorktreeCommitSkip.CLEAN)

    stage_key = run.stage_key if run is not None else ticket.workflow_stage_key
    message = f"{ticket.external_id}: {stage_key}"
    try:
        commit_paths_in(ticket_root, message, paths)
    except ValueError as exc:
        return _fail(str(exc), paths=paths, attempted=True)

    try:
        leftover = uncommitted_at_landing(session, ticket, workspace)
    except RuntimeError as exc:
        return _fail(str(exc), paths=paths, attempted=True)

    if leftover:
        return _fail(
            f"uncommitted ticket work remains: {', '.join(leftover)}",
            paths=paths,
            attempted=True,
        )
    return RunWorktreeCommit(
        attempted=True,
        committed=True,
        error="",
        paths=paths,
        skipped=WorktreeCommitSkip.NONE,
    )


def _skip_reason(
    session: Session, ticket: Ticket, run: AgentRun | None
) -> WorktreeCommitSkip | None:
    """First matching skip, or None to continue.

    Order: failed_run → missing workspace → commit_off → shared checkout → live_writer.
    """
    if run is not None and run.status not in _COMMITTABLE:
        return WorktreeCommitSkip.FAILED_RUN
    workspace = workspace_for_ticket(session, ticket)
    if workspace is None:
        return WorktreeCommitSkip.NOT_TICKET_WORKTREE
    if not resolve_git_automation(workspace, ticket).commit:
        return WorktreeCommitSkip.COMMIT_OFF
    if resolve_ticket_root(session, ticket, workspace) == resolve_workspace_root(workspace):
        return WorktreeCommitSkip.NOT_TICKET_WORKTREE
    worktree_id = _live_writer_worktree_id(session, ticket, run)
    if worktree_id and _has_live_writer(session, worktree_id, run):
        return WorktreeCommitSkip.LIVE_WRITER
    return None


def _skip(reason: WorktreeCommitSkip) -> RunWorktreeCommit:
    return RunWorktreeCommit(
        attempted=False,
        committed=False,
        error="",
        paths=(),
        skipped=reason,
    )


def _fail(error: str, *, paths: tuple[str, ...] = (), attempted: bool = False) -> RunWorktreeCommit:
    return RunWorktreeCommit(
        attempted=attempted,
        committed=False,
        error=error,
        paths=paths,
        skipped=WorktreeCommitSkip.NONE,
    )


def _live_writer_worktree_id(session: Session, ticket: Ticket, run: AgentRun | None) -> str:
    if run is not None and run.worktree_id:
        return run.worktree_id
    worktree = session.exec(
        select(Worktree)
        .where(Worktree.ticket_id == ticket.id)
        .where(Worktree.state == WorktreeState.ACTIVE)
        .where(Worktree.chat_session_id.is_(None))
        .order_by(Worktree.created_at.desc())
    ).first()
    return worktree.id if worktree else ""


def _has_live_writer(session: Session, worktree_id: str, run: AgentRun | None) -> bool:
    stmt = select(AgentRun).where(
        AgentRun.worktree_id == worktree_id,
        AgentRun.status == RunStatus.RUNNING,
    )
    if run is not None:
        stmt = stmt.where(AgentRun.id != run.id)
    return session.exec(stmt).first() is not None
