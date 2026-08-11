"""Retiring a ticket's worktree, on completion and after a crash.

Worktrees are cheap to create and easy to leak: the queue has leaked both slots
and directories before, and a server restart mid-run leaves a row saying
"active" for a tree nothing is working in. Two entry points close that:
`release_ticket_worktree` when a ticket reaches a terminal state, and
`reconcile_worktrees` at startup for everything the last process left behind.

Uncommitted work is never destroyed. A tree with local changes is kept and
logged instead of removed — a ticket marked done by hand while an agent's edits
sit unstaged is exactly when someone wants those files back.
"""

from __future__ import annotations

import logging
from pathlib import Path

from loregarden.core.state_machine import StateMachine
from loregarden.models.domain import Ticket, Workspace, Worktree, WorktreeState
from loregarden.services.git_subprocess import run_git
from loregarden.services.workspace_paths import resolve_workspace_root
from loregarden.services.worktree_service import WorktreeService
from sqlmodel import Session, select

logger = logging.getLogger(__name__)


def _is_dirty(path: Path) -> bool:
    """True when the tree holds changes no commit has captured."""
    status = run_git(
        ["status", "--porcelain"],
        cwd=str(path),
        check=False,
        capture_output=True,
        text=True,
    )
    if status.returncode != 0:
        # An unreadable tree is not a tree we should be deleting.
        return True
    return bool(status.stdout.strip())


def _service_for(session: Session, workspace_id: str) -> WorktreeService | None:
    workspace = session.get(Workspace, workspace_id)
    if not workspace:
        return None
    return WorktreeService(session, repo_path=str(resolve_workspace_root(workspace)))


def _retire(session: Session, service: WorktreeService, worktree: Worktree) -> bool:
    path = Path(worktree.worktree_path) if worktree.worktree_path else None
    if path and path.is_dir() and _is_dirty(path):
        logger.warning(
            "Worktree %s at %s has uncommitted changes; leaving it on disk",
            worktree.id,
            path,
        )
        return False
    return service.cleanup_worktree(worktree)


def release_ticket_worktree(session: Session, ticket: Ticket) -> bool:
    """Remove the worktree a finished ticket was using. Returns True if removed.

    The ticket's branch survives — its commits live in the shared repository's
    object store, so a PR opened later still has them. Only the checkout goes.
    """
    if ticket.state not in StateMachine.TERMINAL_TICKET_STATES:
        return False

    service = _service_for(session, ticket.workspace_id)
    if not service:
        return False

    worktree = service.active_worktree_for_ticket(ticket.id)
    if not worktree:
        return False
    return _retire(session, service, worktree)


def reconcile_worktrees(session: Session) -> int:
    """Settle worktrees the last process left active. Returns how many changed.

    Three cases, and only the first two are this function's business: the
    directory is gone (the row is stale bookkeeping), or the ticket has since
    finished (nothing will ever use the tree again). A live tree for an
    unfinished ticket is left alone — the orchestration resume path picks it
    back up, and deleting it would throw away the work that survived the crash.
    """
    rows = session.exec(select(Worktree).where(Worktree.state == WorktreeState.ACTIVE)).all()
    settled = 0
    for worktree in rows:
        service = _service_for(session, worktree.workspace_id)
        if not service:
            continue

        path = Path(worktree.worktree_path) if worktree.worktree_path else None
        if path is None or not path.is_dir():
            logger.info("Worktree %s is recorded active but gone from disk", worktree.id)
            service.cleanup_worktree(worktree)
            settled += 1
            continue

        ticket = session.get(Ticket, worktree.ticket_id) if worktree.ticket_id else None
        if ticket and ticket.state in StateMachine.TERMINAL_TICKET_STATES:
            if _retire(session, service, worktree):
                settled += 1

    if settled:
        logger.info("Reconciled %d orphaned worktree(s) at startup", settled)
    return settled
