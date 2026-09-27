"""Whether a workspace carries loregarden's pre-commit gates and AGENTS.md section.

Reading runs each installer's ``--check``; posting installs or refreshes one.
Installing writes into the workspace's repository — `lefthook.yml` or
`AGENTS.md`, uncommitted — so it is a REST call a person makes from the page,
behind the API token. Agents reach the hooks half through
`loregarden_check_organization action=install_hooks`, which goes to the inbox.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from loregarden.db.session import get_session
from loregarden.models.domain import Workspace
from loregarden.services import workspace_integration as integration
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
