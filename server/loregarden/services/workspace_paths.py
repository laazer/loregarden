"""Resolve per-workspace filesystem roots.

Each workspace's ``repo_path`` points at the target project checkout. Agent runs
execute with that repo as cwd and load ``agent_context/`` from there.
"""

from __future__ import annotations

import logging
from pathlib import Path

from loregarden.config import settings
from loregarden.models.domain import AgentRun, Workspace, Worktree
from sqlmodel import Session

logger = logging.getLogger(__name__)


def resolve_run_root(session: Session, run: AgentRun, workspace_root: Path) -> Path:
    """The checkout one run executes in.

    Its worktree when it has one, and the shared workspace checkout otherwise.
    A recorded worktree whose directory is gone falls back rather than failing:
    the run's work still belongs somewhere, and a missing worktree is a cleanup
    race, not a reason to fail the ticket.
    """
    if not run.worktree_id:
        return workspace_root

    worktree = session.get(Worktree, run.worktree_id)
    if not worktree or not worktree.worktree_path:
        return workspace_root

    path = Path(worktree.worktree_path)
    if not path.is_dir():
        logger.warning(
            "Worktree %s for run %s is missing at %s; running in the workspace checkout",
            worktree.id,
            run.id,
            path,
        )
        return workspace_root
    return path


def resolve_workspace_root(workspace: Workspace) -> Path:
    raw = (workspace.repo_path or ".").strip()
    path = Path(raw)
    if not path.is_absolute():
        path = settings.repo_root / path
    return path.resolve()


def resolve_agent_context_dir(workspace: Workspace) -> Path:
    return resolve_workspace_root(workspace) / "agent_context"


def workspace_repo_exists(workspace: Workspace) -> bool:
    return resolve_workspace_root(workspace).is_dir()
