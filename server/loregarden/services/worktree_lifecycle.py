"""Retiring a ticket's worktree, on completion and after a crash.

Worktrees are cheap to create and easy to leak: the queue has leaked both slots
and directories before, and a server restart mid-run leaves a row saying
"active" for a tree nothing is working in. Two entry points close that:
`release_ticket_worktree` when a ticket reaches a terminal state, and
`reconcile_worktrees` at startup for everything the last process left behind.

Uncommitted work is never destroyed. Removing a checkout is only safe because
the branch's commits outlive it in the shared object store, and that argument
has two premises the destructive step now checks instead of assuming: the tree
holds no changes (tracked *or* untracked), and the branch holds commits its base
does not. The external-harness path never commits at stage transitions, so a
ticket driven that way reaches its terminal state with both premises false — an
observed case had 1164 uncommitted lines across seven files and an empty
`git log base..HEAD`. Either premise failing keeps the tree on disk, with a log
line naming which one.
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
    """True when the tree holds changes no commit has captured.

    `--untracked-files=all` is explicit rather than implied: untracked files are
    the majority of what an agent produces before its first commit (three of the
    seven files in the observed near-miss), and `status.showUntrackedFiles=no` in
    a repo's config would otherwise turn this guard into a rubber stamp.
    """
    status = run_git(
        ["status", "--porcelain", "--untracked-files=all"],
        cwd=str(path),
        check=False,
        capture_output=True,
        text=True,
    )
    if status.returncode != 0:
        # An unreadable tree is not a tree we should be deleting.
        return True
    return bool(status.stdout.strip())


def _base_ref(path: Path, parent_branch: str) -> str | None:
    """The ref the ticket branch was cut from, as this checkout can name it.

    The remote-tracking ref is preferred: it is what a later PR would be opened
    against, so it is the ref that decides whether a commit is worth preserving.
    The local branch is the fallback for a repository with no remote.
    """
    for ref in (f"origin/{parent_branch}", parent_branch):
        probe = run_git(
            ["rev-parse", "--verify", "--quiet", ref],
            cwd=str(path),
            check=False,
            capture_output=True,
            text=True,
        )
        if probe.returncode == 0:
            return ref
    return None


def _has_preserved_commits(path: Path, worktree: Worktree) -> bool:
    """True when the branch holds commits its base does not.

    Those commits are the entirety of what survives removing the checkout. The
    question is asked of the *branch*, not of the tree's HEAD, because a branch
    ref is what keeps commits reachable once the worktree is gone — work
    committed on a detached HEAD counts for nothing here, and should not.

    A base that cannot be resolved, or a `rev-list` that fails, answers False:
    an unprovable premise is not a satisfied one.
    """
    base = _base_ref(path, worktree.parent_branch)
    if base is None:
        return False
    counted = run_git(
        ["rev-list", "--count", f"{base}..{worktree.branch or 'HEAD'}"],
        cwd=str(path),
        check=False,
        capture_output=True,
        text=True,
    )
    if counted.returncode != 0:
        return False
    total = counted.stdout.strip()
    return total.isdigit() and int(total) > 0


def _service_for(session: Session, workspace_id: str) -> WorktreeService | None:
    workspace = session.get(Workspace, workspace_id)
    if not workspace:
        return None
    return WorktreeService(session, repo_path=str(resolve_workspace_root(workspace)))


def _retire(session: Session, service: WorktreeService, worktree: Worktree) -> bool:
    path = Path(worktree.worktree_path) if worktree.worktree_path else None
    if path and path.is_dir():
        if _is_dirty(path):
            logger.warning(
                "Worktree %s at %s has uncommitted changes; leaving it on disk",
                worktree.id,
                path,
            )
            return False
        if not _has_preserved_commits(path, worktree):
            logger.warning(
                "Worktree %s at %s: branch %s has no commits beyond %s, so removing "
                "it would preserve nothing; leaving it on disk",
                worktree.id,
                path,
                worktree.branch or "HEAD",
                worktree.parent_branch,
            )
            return False
    return service.cleanup_worktree(worktree)


def release_ticket_worktree(session: Session, ticket: Ticket) -> bool:
    """Remove the worktree a finished ticket was using. Returns True if removed.

    The ticket's branch survives — its commits live in the shared repository's
    object store, so a PR opened later still has them. Only the checkout goes.
    That holds exactly as far as `_retire` proves it does: a tree with local
    changes, or a branch carrying no commits of its own, is kept instead.
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
