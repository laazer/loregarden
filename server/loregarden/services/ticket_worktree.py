"""Which checkout a ticket's run executes in.

Runs used to execute in the shared workspace checkout and switch its branch
with `checkout -B` on the way in. That is the mechanism behind two known
failure modes: a crash mid-run leaves the shared tree on a half-applied ticket
branch, and two tickets cannot run at once without overwriting each other's
files.

This module puts the ticket's own worktree in front of that: the first stage
cuts it (branch and all), every later stage reuses it, and the shared checkout
is never touched. Policy still decides — a workspace with `git.worktree` off
keeps the old in-place behaviour.

Separate from `workspace_paths` because that module is imported *by*
`worktree_service`; resolving a worktree here rather than there is what keeps
the import graph acyclic.
"""

from __future__ import annotations

import logging
from pathlib import Path

from loregarden.models.domain import AgentRun, Ticket, Workspace
from loregarden.services.git_automation_config import resolve_git_automation
from loregarden.services.workspace_paths import resolve_run_root, resolve_workspace_root
from loregarden.services.worktree_service import WorktreeService
from sqlmodel import Session

logger = logging.getLogger(__name__)


def resolve_ticket_root(session: Session, ticket: Ticket, workspace: Workspace) -> Path:
    """Where this ticket's branch and work live on disk right now.

    Read-only counterpart to :func:`resolve_execution_root`: operator actions
    (commit, push, open a PR) have to reach the tree the stages actually wrote
    to, but must not conjure a worktree for a ticket that never ran.
    """
    workspace_root = resolve_workspace_root(workspace)
    service = WorktreeService(session, repo_path=str(workspace_root))
    worktree = service.active_worktree_for_ticket(ticket.id)
    if worktree and worktree.worktree_path and Path(worktree.worktree_path).is_dir():
        return Path(worktree.worktree_path)
    return workspace_root


def resolve_execution_root(
    session: Session,
    run: AgentRun,
    ticket: Ticket,
    workspace: Workspace,
) -> Path:
    """The directory this run should execute in, creating a worktree if needed.

    A run that already has a worktree (the parallel queue and stage fan-out
    both assign one before dispatch) keeps it. Otherwise the ticket's shared
    worktree is created on first use and reused by every later stage.

    Falls back to the shared checkout rather than failing the run: a worktree
    that cannot be cut is a degraded run, not a dead ticket.
    """
    workspace_root = resolve_workspace_root(workspace)
    if run.worktree_id:
        return resolve_run_root(session, run, workspace_root)

    config = resolve_git_automation(workspace, ticket)
    if not config.worktree:
        return workspace_root

    service = WorktreeService(session, repo_path=str(workspace_root))
    worktree = service.get_or_create_for_ticket(ticket, run.id, parent_branch=config.base_branch)
    if not worktree:
        logger.warning(
            "Could not create a worktree for ticket %s; running in the shared checkout %s",
            ticket.id,
            workspace_root,
        )
        return workspace_root

    run.worktree_id = worktree.id
    session.add(run)
    session.commit()
    return Path(worktree.worktree_path)
