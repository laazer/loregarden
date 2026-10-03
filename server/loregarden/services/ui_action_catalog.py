"""The UI actions an agent may ask an open Loregarden tab to perform.

The server owns this list and every action's effect. A browser tab only says
which of these it can perform right now (a ticket's actions exist while a
ticket is open), so a tab can never widen what an action is allowed to do —
the policy is decided here, from the catalog, before a tab is ever asked.

`client/src/lib/agentActions/catalog.ts` mirrors the names and argument
shapes; `test_ui_actions.py` fails when the two drift.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from loregarden.models.domain.enums import TicketState
from pydantic import BaseModel, ConfigDict, Field


class UiAction(StrEnum):
    NAVIGATE_PAGE = "navigate.page"
    TICKET_OPEN = "ticket.open"
    TICKET_UPDATE = "ticket.update"
    TICKET_SET_STATE = "ticket.set_state"
    TICKET_TRIGGER_AUTO_FIX = "ticket.trigger_auto_fix"
    WORKSPACE_ARCHIVE = "workspace.archive"
    WORKSPACE_RESTORE = "workspace.restore"
    WORKSPACE_SET_WORKFLOW = "workspace.set_workflow"
    REFERENCE_REPO_ADD = "reference_repo.add"
    REFERENCE_REPO_SYNC = "reference_repo.sync"
    APPROVAL_RESOLVE = "approval.resolve"

    @classmethod
    def try_parse(cls, name: str) -> UiAction | None:
        try:
            return cls(name)
        except ValueError:
            return None


class UiActionEffect(StrEnum):
    """What invoking an action does, and so what it needs before it may run."""

    #: Changes what the operator's tab shows; changes no data. Auto-approved.
    VIEW = "view"
    #: Changes data. Approved exactly as a non-auto-approved MCP tool is: an
    #: approvals row on a gated run, none on an auto_approve run or in chat.
    WRITE = "write"
    #: Never invokable by an agent. Listed so an agent asking "may I?" is told
    #: no by the catalog instead of discovering it by failing.
    HUMAN_ONLY = "human_only"


class UiPage(StrEnum):
    """Top-level pages, as `client/src/lib/appNavigation.ts` names them."""

    HOME = "home"
    CHAT = "chat"
    DASHBOARD = "dashboard"
    INITIATIVES = "initiatives"
    STUDIO = "studio"
    EDITOR = "editor"
    QUEUE = "queue"
    BRANCH_TRIAGE = "branch-triage"
    MCP = "mcp"
    MEMORY = "memory"
    WORKSPACES = "workspaces"


class _Args(BaseModel):
    model_config = ConfigDict(extra="forbid")


class NavigatePageArgs(_Args):
    page: UiPage


class TicketOpenArgs(_Args):
    ticket_id: str = Field(min_length=1, description="Ticket UUID or external id.")


class TicketUpdateArgs(_Args):
    ticket_id: str = Field(min_length=1, description="The ticket open in the tab.")
    title: str | None = None
    description: str | None = None
    acceptance_criteria: list[str] | None = None
    priority: int | None = Field(default=None, ge=1, le=3)


class TicketSetStateArgs(_Args):
    ticket_id: str = Field(min_length=1, description="The ticket open in the tab.")
    state: TicketState


class TicketTriggerAutoFixArgs(_Args):
    ticket_id: str = Field(min_length=1, description="The ticket open in the tab.")


class WorkspaceArgs(_Args):
    workspace_slug: str = Field(min_length=1)


class WorkspaceSetWorkflowArgs(_Args):
    workspace_slug: str = Field(min_length=1)
    template: str = Field(min_length=1, description="A workflow template slug.")


class ReferenceRepoAddArgs(_Args):
    workspace_slug: str = Field(min_length=1, description="The workspace whose picker is open.")
    url: str = Field(min_length=1, description="The repository URL to clone.")
    notes: str = ""


class ReferenceRepoSyncArgs(_Args):
    reference_repo_id: str = Field(min_length=1)


class ApprovalResolveArgs(_Args):
    approval_id: str


@dataclass(frozen=True)
class UiActionSpec:
    action: UiAction
    effect: UiActionEffect
    description: str
    args_model: type[_Args]
    #: Where a tab offers it — what an agent must open first when no tab does.
    offered: str

    def describe(self) -> dict[str, Any]:
        return {
            "action": self.action.value,
            "effect": self.effect.value,
            "description": self.description,
            "offered": self.offered,
            "arguments": self.args_model.model_json_schema(),
        }


CATALOG: dict[UiAction, UiActionSpec] = {
    spec.action: spec
    for spec in (
        UiActionSpec(
            UiAction.NAVIGATE_PAGE,
            UiActionEffect.VIEW,
            "Show one of the app's top-level pages in the operator's tab.",
            NavigatePageArgs,
            offered="in every open tab",
        ),
        UiActionSpec(
            UiAction.TICKET_OPEN,
            UiActionEffect.VIEW,
            "Open a ticket in the operator's tab. The ticket actions below need it open.",
            TicketOpenArgs,
            offered="in every open tab",
        ),
        UiActionSpec(
            UiAction.TICKET_UPDATE,
            UiActionEffect.WRITE,
            "Edit the open ticket's title, description, acceptance criteria or priority.",
            TicketUpdateArgs,
            offered="while that ticket is open (ticket.open)",
        ),
        UiActionSpec(
            UiAction.TICKET_SET_STATE,
            UiActionEffect.WRITE,
            "Set the open ticket's state, as the state picker does.",
            TicketSetStateArgs,
            offered="while that ticket is open (ticket.open)",
        ),
        UiActionSpec(
            UiAction.TICKET_TRIGGER_AUTO_FIX,
            UiActionEffect.WRITE,
            "Launch the CI auto-fix agent for the open ticket.",
            TicketTriggerAutoFixArgs,
            offered="while that ticket is open (ticket.open)",
        ),
        UiActionSpec(
            UiAction.WORKSPACE_ARCHIVE,
            UiActionEffect.WRITE,
            "Archive a workspace from the Workspaces page. Reversible with workspace.restore.",
            WorkspaceArgs,
            offered="on the Workspaces page (navigate.page workspaces)",
        ),
        UiActionSpec(
            UiAction.WORKSPACE_RESTORE,
            UiActionEffect.WRITE,
            "Restore an archived workspace from the Workspaces page.",
            WorkspaceArgs,
            offered="on the Workspaces page (navigate.page workspaces)",
        ),
        UiActionSpec(
            UiAction.WORKSPACE_SET_WORKFLOW,
            UiActionEffect.WRITE,
            "Set a workspace's default workflow template, from the console's workspaces pane.",
            WorkspaceSetWorkflowArgs,
            offered="on the console with that workspace selected (navigate.page dashboard)",
        ),
        UiActionSpec(
            UiAction.REFERENCE_REPO_ADD,
            UiActionEffect.WRITE,
            "Clone a repository into the workspace's reference library, from the open picker.",
            ReferenceRepoAddArgs,
            offered="while that workspace's reference-repo picker is open in Ticket Studio",
        ),
        UiActionSpec(
            UiAction.REFERENCE_REPO_SYNC,
            UiActionEffect.WRITE,
            "Fetch the latest of a reference repository shown in the open picker.",
            ReferenceRepoSyncArgs,
            offered="while a reference-repo picker listing it is open in Ticket Studio",
        ),
        UiActionSpec(
            UiAction.APPROVAL_RESOLVE,
            UiActionEffect.HUMAN_ONLY,
            "Approve or reject an inbox item. The inbox is the human gate on agents' "
            "work, so no agent may resolve one — this is refused, always.",
            ApprovalResolveArgs,
            offered="never to an agent",
        ),
    )
}
