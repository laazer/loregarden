"""Whether a workspace carries loregarden's pre-commit gates and AGENTS.md section.

Reading runs each installer's ``--check``; posting installs or refreshes one.
Installing writes into the workspace's repository — `lefthook.yml` or
`AGENTS.md`, uncommitted — so it is a REST call a person makes from the page,
behind the API token. Its `/pull-request` form writes nothing there: it lands
the same block as a pull request against the repository's default branch. Agents reach the hooks half through
`loregarden_check_organization action=install_hooks`, which goes to the inbox.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from loregarden.db.session import get_session
from loregarden.models.domain import Workspace
from loregarden.services import workspace_integration as integration
from loregarden.services.change_pr import ChangeOutcome, ChangePrError
from loregarden.services.git_subprocess import GhAccountUnavailable, configured_gh_token
from loregarden.services.organization_gate_service import UnknownWorkspaceError, workspace_for_slug
from loregarden.services.workspace_integration import (
    Installer,
    InstallerError,
    InstallerStatus,
    InstallState,
)
from loregarden.services.workspace_paths import resolve_workspace_root
from pydantic import BaseModel
from sqlmodel import Session

router = APIRouter(prefix="/workspace-integration", tags=["instances"])


class InstallerView(BaseModel):
    installer: Installer
    state: InstallState
    detail: str


class WorkspaceIntegrationView(BaseModel):
    slug: str
    repo_root: str
    installers: list[InstallerView]


def _installer_view(found: InstallerStatus) -> InstallerView:
    return InstallerView(installer=found.installer, state=found.state, detail=found.detail)


def _workspace(session: Session, slug: str) -> Workspace:
    try:
        return workspace_for_slug(session, slug)
    except UnknownWorkspaceError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc


def _view(workspace: Workspace) -> WorkspaceIntegrationView:
    return WorkspaceIntegrationView(
        slug=workspace.slug,
        repo_root=str(resolve_workspace_root(workspace)),
        installers=[
            _installer_view(integration.installer_status(workspace, installer))
            for installer in Installer
        ],
    )


@router.get("/{slug}", response_model=WorkspaceIntegrationView)
def workspace_integration(
    slug: str, session: Session = Depends(get_session)
) -> WorkspaceIntegrationView:
    return _view(_workspace(session, slug))


@router.post("/{slug}/{installer}", response_model=WorkspaceIntegrationView)
def install_into_workspace(
    slug: str, installer: Installer, session: Session = Depends(get_session)
) -> WorkspaceIntegrationView:
    workspace = _workspace(session, slug)
    try:
        integration.install(workspace, installer)
    except InstallerError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    return _view(workspace)


class InstallPullRequestView(BaseModel):
    outcome: ChangeOutcome
    #: The pull request's url when opened; what the installer said when it changed nothing.
    detail: str


@router.post("/{slug}/{installer}/pull-request", response_model=InstallPullRequestView)
def install_as_pull_request(
    slug: str, installer: Installer, session: Session = Depends(get_session)
) -> InstallPullRequestView:
    """Open a pull request in the workspace's repository carrying the block.

    409 when there is nothing to install or nowhere to install it, 412 when the
    configured GitHub account has no token here, 502 when a git or gh step
    fails (the message names the step, and any worktree kept for inspection).
    """
    workspace = _workspace(session, slug)
    try:
        token = configured_gh_token()
    except GhAccountUnavailable as exc:
        raise HTTPException(status.HTTP_412_PRECONDITION_FAILED, str(exc)) from exc
    try:
        result = integration.pull_request(workspace, installer, gh_token=token)
    except InstallerError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    except ChangePrError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc
    return InstallPullRequestView(outcome=result.outcome, detail=result.detail)
