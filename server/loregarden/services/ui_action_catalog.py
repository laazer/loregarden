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
from loregarden.models.domain.schemas import WorkspaceRuntimeUpdate
from pydantic import BaseModel, ConfigDict, Field, create_model


class UiAction(StrEnum):
    NAVIGATE_PAGE = "navigate.page"
    TICKET_OPEN = "ticket.open"
    TICKET_UPDATE = "ticket.update"
    TICKET_SET_STATE = "ticket.set_state"
    TICKET_TRIGGER_AUTO_FIX = "ticket.trigger_auto_fix"
    TICKET_MERGE_PULL_REQUEST = "ticket.merge_pull_request"
    WORKSPACE_ARCHIVE = "workspace.archive"
    WORKSPACE_RESTORE = "workspace.restore"
    WORKSPACE_SET_WORKFLOW = "workspace.set_workflow"
    REFERENCE_REPO_ADD = "reference_repo.add"
    REFERENCE_REPO_SYNC = "reference_repo.sync"
    TICKET_START_STAGE = "ticket.start_stage"
    TICKET_STOP = "ticket.stop"
    TICKET_SET_RUNTIME = "ticket.set_runtime"
    TRIAGE_SET_RUNTIME = "triage.set_runtime"
    WORKSPACE_SET_RUNTIME = "workspace.set_runtime"
    WORKSPACE_CREATE = "workspace.create"
    WORKSPACE_CREATE_REPOSITORY = "workspace.create_repository"
    RUN_SEND_MESSAGE = "run.send_message"
    RUN_CANCEL = "run.cancel"
    QUEUE_PROMOTE = "queue.promote"
    QUEUE_CANCEL = "queue.cancel"
    CAPACITY_RELEASE = "capacity.release"
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


class TicketMergePullRequestArgs(_Args):
    ticket_id: str = Field(min_length=1, description="The ticket open in the tab.")
    number: int = Field(gt=0, description="The pull request number the tab shows.")
    head_sha: str = Field(
        min_length=7, description="The head commit you checked; refused if the PR moved."
    )


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


class TicketStartStageArgs(_Args):
    ticket_id: str = Field(min_length=1, description="The ticket open in the tab.")
    stage_key: str = Field(min_length=1, description="The workflow stage to run on its own.")


class TicketArgs(_Args):
    ticket_id: str = Field(min_length=1, description="The ticket open in the tab.")


#: A runtime change names only what changes; everything left out keeps its
#: current value. Built from the update schema so a new runtime field is
#: offered here without a second list to keep in step.
_RUNTIME_FIELDS: dict[str, Any] = {
    name: (field.annotation | None, None)
    for name, field in WorkspaceRuntimeUpdate.model_fields.items()
}
TicketRuntimeArgs = create_model(
    "TicketRuntimeArgs",
    __base__=_Args,
    ticket_id=(str, Field(min_length=1, description="The ticket the runtime belongs to.")),
    **_RUNTIME_FIELDS,
)
WorkspaceRuntimeArgs = create_model(
    "WorkspaceRuntimeArgs",
    __base__=_Args,
    workspace_slug=(str, Field(min_length=1)),
    **_RUNTIME_FIELDS,
)


class WorkspaceCreateArgs(_Args):
    slug: str = Field(min_length=1)
    name: str = Field(min_length=1)
    repo_path: str = ""
    workflow_template_slug: str = ""


class RunArgs(_Args):
    run_id: str = Field(min_length=1)


class RunSendMessageArgs(_Args):
    run_id: str = Field(min_length=1)
    message: str = Field(min_length=1, description="Shown to the run, and in it, as from an agent.")


class CapacityReleaseArgs(_Args):
    lease_id: str = Field(min_length=1)
    reason: str = Field(min_length=1, description="Kept on the lease, so its owner knows why.")


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
            UiAction.TICKET_MERGE_PULL_REQUEST,
            UiActionEffect.WRITE,
            "Merge and clean up: squash-merge the open ticket's PR at head_sha, then remove "
            "its branch's clean worktrees, local branch and GitHub branch. Refused unless "
            "GitHub says it can merge now. Irreversible. Without a tab, use "
            "loregarden_merge_pull_request.",
            TicketMergePullRequestArgs,
            offered="while that ticket's PR tab shows Merge and clean up (ticket.open, PR tab)",
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
            UiAction.TICKET_START_STAGE,
            UiActionEffect.WRITE,
            "Run one stage of the open ticket on its own, as the stage's Run button does.",
            TicketStartStageArgs,
            offered="while that ticket is open (ticket.open)",
        ),
        UiActionSpec(
            UiAction.TICKET_STOP,
            UiActionEffect.WRITE,
            "Stop the open ticket's running work.",
            TicketArgs,
            offered="while that ticket is open (ticket.open)",
        ),
        UiActionSpec(
            UiAction.TICKET_SET_RUNTIME,
            UiActionEffect.WRITE,
            "Change the adapter, model or effort the open ticket's agent runs use. Only the fields given change.",
            TicketRuntimeArgs,
            offered="while that ticket is open (ticket.open)",
        ),
        UiActionSpec(
            UiAction.TRIAGE_SET_RUNTIME,
            UiActionEffect.WRITE,
            "Change the runtime of a branch's triage chat for its linked ticket. Only the fields given change.",
            TicketRuntimeArgs,
            offered="on Branch triage, for a branch with a linked ticket (navigate.page branch-triage)",
        ),
        UiActionSpec(
            UiAction.WORKSPACE_SET_RUNTIME,
            UiActionEffect.WRITE,
            "Change a workspace's default runtime. Only the fields given change.",
            WorkspaceRuntimeArgs,
            offered="in every open tab",
        ),
        UiActionSpec(
            UiAction.WORKSPACE_CREATE,
            UiActionEffect.WRITE,
            "Add a workspace record. Its repository is a separate step: workspace.create_repository.",
            WorkspaceCreateArgs,
            offered="on the Workspaces page (navigate.page workspaces)",
        ),
        UiActionSpec(
            UiAction.WORKSPACE_CREATE_REPOSITORY,
            UiActionEffect.WRITE,
            "Create the git repository for a workspace whose card offers it.",
            WorkspaceArgs,
            offered="on the Workspaces page, on that workspace's card (navigate.page workspaces)",
        ),
        UiActionSpec(
            UiAction.RUN_SEND_MESSAGE,
            UiActionEffect.WRITE,
            "Send a steering message to a running stage. It is marked as coming from an agent.",
            RunSendMessageArgs,
            offered="while that run's steering composer is on screen",
        ),
        UiActionSpec(
            UiAction.RUN_CANCEL,
            UiActionEffect.WRITE,
            "Stop a running stage, as its 'Stop this run' control does.",
            RunArgs,
            offered="while that run's steering composer is on screen",
        ),
        UiActionSpec(
            UiAction.QUEUE_PROMOTE,
            UiActionEffect.WRITE,
            "Move a queued run to the front of the queue.",
            RunArgs,
            offered="on the Queue page, for a run it lists (navigate.page queue)",
        ),
        UiActionSpec(
            UiAction.QUEUE_CANCEL,
            UiActionEffect.WRITE,
            "Remove a queued run from the queue.",
            RunArgs,
            offered="on the Queue page, for a run it lists (navigate.page queue)",
        ),
        UiActionSpec(
            UiAction.CAPACITY_RELEASE,
            UiActionEffect.WRITE,
            "End a machine-capacity lease: release a holder's grant, or drop a waiter "
            "from the line. Its process is not stopped — a holder keeps running "
            "unaccounted, and a live waiter queues again at the back.",
            CapacityReleaseArgs,
            offered="on the Queue page's Machine view, for a lease it lists (navigate.page queue)",
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
