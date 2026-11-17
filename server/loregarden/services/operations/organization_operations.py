"""The organization gates and loregarden's managed blocks in a workspace, as
operations: an agent's `check_organization`, and the page's integration view
and install.

They overlap in the hook block. An agent reads it with `hooks_status` and
writes it with `install_hooks` (gated by the inbox, by action, in
`tool_auto_approve`); the page reads every installer's state and installs one.
The two installs had drifted: the agent's refused to overwrite uncommitted
edits to `lefthook.yml`, the page's did not. Both now run
`install_guarded`, so neither can clobber a person's edit.

`action=check`, running the gates over a diff, is the agent's alone; the page
installs blocks, it does not run gates.
"""

from __future__ import annotations

from loregarden.models.domain import Workspace
from loregarden.services import workspace_integration as integration
from loregarden.services.operations.spec import (
    Caller,
    Effect,
    Operation,
    OperationId,
    Transport,
)
from loregarden.services.organization_gate_service import (
    OrganizationAction,
    OrganizationScope,
    install_guarded,
    run_organization_gate,
    workspace_for_slug,
)
from loregarden.services.workspace_integration import Installer, InstallState
from loregarden.services.workspace_paths import resolve_workspace_root
from pydantic import BaseModel, ConfigDict, Field
from sqlmodel import Session


class OrganizationCheck(BaseModel):
    """The tool's arguments; unknown keys are refused, as its schema says."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    workspace_slug: str = Field(min_length=1)
    action: OrganizationAction = OrganizationAction.CHECK
    scope: OrganizationScope = OrganizationScope.WORKTREE


class WorkspaceKey(BaseModel):
    slug: str


class WorkspaceInstall(WorkspaceKey):
    installer: Installer


class InstallerView(BaseModel):
    installer: Installer
    state: InstallState
    detail: str


class WorkspaceIntegrationView(BaseModel):
    slug: str
    repo_root: str
    installers: list[InstallerView]


def _view(workspace: Workspace) -> WorkspaceIntegrationView:
    return WorkspaceIntegrationView(
        slug=workspace.slug,
        repo_root=str(resolve_workspace_root(workspace)),
        installers=[
            InstallerView(installer=found.installer, state=found.state, detail=found.detail)
            for found in (
                integration.installer_status(workspace, installer) for installer in Installer
            )
        ],
    )


def _check(session: Session, request: OrganizationCheck, _caller: Caller) -> dict:
    workspace = workspace_for_slug(session, request.workspace_slug)
    return run_organization_gate(workspace, request.action, request.scope).as_payload()


def _integration(session: Session, key: WorkspaceKey, _caller: Caller) -> WorkspaceIntegrationView:
    return _view(workspace_for_slug(session, key.slug))


def _install(
    session: Session, request: WorkspaceInstall, _caller: Caller
) -> WorkspaceIntegrationView:
    workspace = workspace_for_slug(session, request.slug)
    install_guarded(workspace, request.installer)
    return _view(workspace)


_PAGE_ONLY = {
    Transport.MCP: "agents read and install the hook block through "
    "loregarden_check_organization (hooks_status, install_hooks)",
    Transport.CLI: "the CLI runs MCP's tools, which reach the hook block through "
    "organization_check",
}

ORGANIZATION_OPERATIONS = (
    Operation(
        id=OperationId.ORGANIZATION_CHECK,
        # `install_hooks` writes another repository's lefthook.yml.
        effect=Effect.WRITE,
        args=OrganizationCheck,
        handler=_check,
        unavailable_on={
            Transport.REST: "the page installs blocks through workspace_integration_install "
            "and runs no gates; checking a diff is an agent's question before a stage",
        },
    ),
    Operation(
        id=OperationId.WORKSPACE_INTEGRATION,
        effect=Effect.READ,
        args=WorkspaceKey,
        handler=_integration,
        unavailable_on=_PAGE_ONLY,
    ),
    Operation(
        id=OperationId.WORKSPACE_INTEGRATION_INSTALL,
        effect=Effect.WRITE,
        args=WorkspaceInstall,
        handler=_install,
        unavailable_on=_PAGE_ONLY,
    ),
)
