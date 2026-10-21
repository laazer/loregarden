"""Live workspaces on temp-directory repos, found and archived.

`db.workspace_repo_guard` refuses the write; this is the invariant
`live_workspaces_have_real_repositories` (lg-milestone-that-1319) catching a
row that got past it, or that predates it. Asked only of the live database:
a fixture's or a sandbox's workspaces are theirs to put where they like.
"""

from __future__ import annotations

import logging

from loregarden.db.workspace_repo_guard import is_temp_repo, writes_live_database
from loregarden.models.domain import Workspace
from loregarden.models.domain.enums import utcnow
from loregarden.services.active_workspaces import active_workspaces
from sqlmodel import Session

logger = logging.getLogger(__name__)


def temp_workspaces(session: Session) -> list[Workspace]:
    """Unarchived workspaces whose repo is in a temp directory, on the live database."""
    if not writes_live_database(session.connection()):
        return []
    return [ws for ws in active_workspaces(session) if is_temp_repo(ws.repo_path)]


def archive_workspaces(session: Session, workspaces: list[Workspace]) -> int:
    """Archive each of ``workspaces``; how many were. Their tickets are kept."""
    for workspace in workspaces:
        workspace.archived_at = utcnow()
        session.add(workspace)
        logger.warning(
            "Archived workspace %s: its repo %s is in a temp directory",
            workspace.slug,
            workspace.repo_path,
        )
    if workspaces:
        session.commit()
    return len(workspaces)
