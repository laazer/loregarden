"""Resolve per-workspace filesystem roots.

Each workspace's ``repo_path`` points at the target project checkout. Agent runs
execute with that repo as cwd and load ``agent_context/`` from there.
"""

from __future__ import annotations

from pathlib import Path

from loregarden.config import settings
from loregarden.models.domain import Workspace


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
