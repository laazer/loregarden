"""The workspace dashboard's own reads: repo link, open PRs, and the catch-up digest.

Everything else the dashboard shows (tickets, approvals, instances, milestones,
branch cleanup) already has an endpoint, so it is read from those.

Its own router rather than more routes in `workspaces.py`. Plain `def`s: two of
these shell out, and FastAPI runs sync handlers in its threadpool, so a slow
GitHub cannot stall the event loop.
"""

from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from loregarden.db.session import get_session
from loregarden.models.domain import ActivityHighlightKind, Workspace
from loregarden.models.domain.enums import comparable_utc, utcnow
from loregarden.services.ci_checks import GitHubReadError
from loregarden.services.workspace_activity import WorkspaceActivity, workspace_activity
from loregarden.services.workspace_pull_requests import (
    WorkspacePullRequests,
    workspace_pull_requests,
)
from loregarden.services.workspace_remote import WorkspaceRemote, workspace_remote
from sqlmodel import Session, select

router = APIRouter(prefix="/workspaces", tags=["workspace-dashboard"])

#: The longest window the digest answers; past it, read the ticket's own history.
MAX_ACTIVITY_WINDOW = timedelta(days=180)


def _workspace(session: Session, slug: str) -> Workspace:
    workspace = session.exec(select(Workspace).where(Workspace.slug == slug)).first()
    if workspace is None:
        raise HTTPException(404, f"Workspace not found: {slug}")
    return workspace


@router.get("/{slug}/remote", response_model=WorkspaceRemote)
def get_workspace_remote(slug: str, session: Session = Depends(get_session)) -> WorkspaceRemote:
    return workspace_remote(_workspace(session, slug))


@router.get("/{slug}/pull-requests", response_model=WorkspacePullRequests)
def get_workspace_pull_requests(
    slug: str, session: Session = Depends(get_session)
) -> WorkspacePullRequests:
    workspace = _workspace(session, slug)
    try:
        return workspace_pull_requests(session, workspace)
    except GitHubReadError as exc:
        raise HTTPException(502, f"Could not list pull requests from GitHub: {exc}") from exc


@router.get("/{slug}/activity", response_model=WorkspaceActivity)
def get_workspace_activity(
    slug: str,
    since: datetime = Query(description="UTC; events at or after this moment."),
    kind: list[ActivityHighlightKind] = Query(
        default=[], description="Only tickets with a highlight of these kinds."
    ),
    session: Session = Depends(get_session),
) -> WorkspaceActivity:
    workspace = _workspace(session, slug)
    # A naive `since` is UTC, as every stored timestamp is.
    since = comparable_utc(since).astimezone(UTC)
    now = utcnow()
    if since > now:
        raise HTTPException(422, "since is in the future")
    if now - since > MAX_ACTIVITY_WINDOW:
        raise HTTPException(422, f"since is more than {MAX_ACTIVITY_WINDOW.days} days ago")
    return workspace_activity(session, workspace, since, frozenset(kind))
