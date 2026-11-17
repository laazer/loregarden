"""Two-way sync between tickets and GitHub issues, as operations: a ticket's
link, publishing a ticket, syncing one ticket, and syncing a workspace.

These were written twice, as the `loregarden_sync_github_issues` tool's actions
and as routes under `/api/tickets/{id}/github-issue` and
`/api/workspaces/{slug}/github-issues/sync`, and had drifted: each side found
the ticket by UUID alone, so an external id was "not found" on both. Every
operation now resolves a ticket by UUID or external id. Syncing a ticket with
no link is refused the same way on both, as `IssueNotLinked`, which the page
reads as a 404 and an agent as an error naming the next step.

The tool keeps its one name and `action` argument, so it binds to
`github_issues`, which runs the same function as the matching route. Each
action's behaviour lives in one function here.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from loregarden.models.domain import (
    ConflictPolicy,
    GithubIssueLinkView,
    LinkSyncResult,
    Ticket,
    WorkItemType,
    WorkspaceSyncResult,
)
from loregarden.services.github_issue_sync import (
    link_for_ticket,
    publish_ticket,
    sync_link,
    sync_workspace,
)
from loregarden.services.operations.spec import (
    Caller,
    Effect,
    Operation,
    OperationId,
    Transport,
)
from loregarden.services.orchestration_callbacks import OrchestrationCallbackService
from loregarden.services.organization_gate_service import workspace_for_slug
from pydantic import BaseModel, ConfigDict
from sqlmodel import Session


class GithubIssueAction(StrEnum):
    #: Read a ticket's link. Touches nothing.
    STATUS = "status"
    #: Open an issue for an unlinked ticket and link them.
    PUBLISH = "publish"
    #: Sync one linked ticket with its issue.
    SYNC = "sync"
    #: Sync every link in a workspace; import unlinked open issues when a parent is given.
    SYNC_WORKSPACE = "sync_workspace"


class IssueNotLinked(LookupError):  # noqa: N818 — reads as the condition, like StopIteration
    """The ticket has no GitHub issue to sync with."""


class TicketIssueKey(BaseModel):
    #: A UUID or an external id.
    ticket_id: str


class TicketIssueSync(TicketIssueKey):
    policy: ConflictPolicy = ConflictPolicy.REPORT


class WorkspaceIssueSync(BaseModel):
    workspace_slug: str
    #: Where unlinked open issues are imported. Blank imports nothing.
    import_parent_ticket_id: str = ""
    import_work_item_type: WorkItemType = WorkItemType.BUG
    #: Only import issues carrying this label. Blank imports every open issue.
    import_label: str = ""
    policy: ConflictPolicy = ConflictPolicy.REPORT


class GithubIssueRequest(BaseModel):
    """The tool's arguments; unknown keys are refused, as its schema says."""

    model_config = ConfigDict(extra="forbid", use_attribute_docstrings=True)

    action: GithubIssueAction
    """What to do."""
    ticket_id: str = ""
    """Ticket id — for status, publish and sync."""
    workspace_slug: str = ""
    """Workspace slug — for sync_workspace."""
    policy: ConflictPolicy = ConflictPolicy.REPORT
    """Conflicting fields: report (default), local wins, remote wins."""
    import_parent_ticket_id: str = ""
    """sync_workspace: import unlinked open issues under this ticket."""
    import_work_item_type: WorkItemType = WorkItemType.BUG
    """sync_workspace: type for imported tickets (default bug)."""
    import_label: str = ""
    """sync_workspace: only import issues with this label."""


@dataclass(frozen=True)
class IssueLinkStatus:
    ticket_id: str
    link: GithubIssueLinkView | None


def _ticket(session: Session, ticket_id: str) -> Ticket:
    return OrchestrationCallbackService(session).resolve_ticket(ticket_id=ticket_id)


def _refused(exc: LookupError) -> ValueError:
    """The services raise `LookupError` for a request that names something
    unusable (a ticket with no workspace, an import parent that is not there).
    That is the caller's request refused, so it reads as one."""
    return ValueError(str(exc))


def _status(session: Session, key: TicketIssueKey, _caller: Caller) -> IssueLinkStatus:
    ticket = _ticket(session, key.ticket_id)
    link = link_for_ticket(session, ticket.id)
    view = GithubIssueLinkView.model_validate(link, from_attributes=True) if link else None
    return IssueLinkStatus(ticket_id=ticket.id, link=view)


def _publish(session: Session, key: TicketIssueKey, _caller: Caller) -> LinkSyncResult:
    ticket = _ticket(session, key.ticket_id)
    try:
        return publish_ticket(session, ticket)
    except LookupError as exc:
        raise _refused(exc) from exc


def _sync(session: Session, request: TicketIssueSync, _caller: Caller) -> LinkSyncResult:
    ticket = _ticket(session, request.ticket_id)
    link = link_for_ticket(session, ticket.id)
    if link is None:
        raise IssueNotLinked(
            f"{ticket.external_id} is not linked to a GitHub issue; publish it first"
        )
    return sync_link(session, link, policy=request.policy)


def _sync_workspace(
    session: Session, request: WorkspaceIssueSync, _caller: Caller
) -> WorkspaceSyncResult:
    workspace = workspace_for_slug(session, request.workspace_slug)
    try:
        return sync_workspace(
            session,
            workspace,
            import_parent_ticket_id=request.import_parent_ticket_id,
            import_work_item_type=request.import_work_item_type,
            import_label=request.import_label,
            policy=request.policy,
        )
    except LookupError as exc:
        raise _refused(exc) from exc


def _required(value: str, field: str, action: GithubIssueAction) -> None:
    if not value:
        raise ValueError(f"{field} is required for action {action.value}")


def _agent_status(status: IssueLinkStatus) -> dict[str, Any]:
    return {
        "ticket_id": status.ticket_id,
        "linked": status.link is not None,
        "link": status.link.model_dump(mode="json") if status.link else None,
    }


def _dispatch(session: Session, request: GithubIssueRequest, caller: Caller) -> dict[str, Any]:
    """The tool's actions, each running the function its route runs."""
    if request.action is GithubIssueAction.SYNC_WORKSPACE:
        _required(request.workspace_slug, "workspace_slug", request.action)
        workspace_sync = WorkspaceIssueSync.model_validate(
            request.model_dump(exclude={"action", "ticket_id"})
        )
        return _sync_workspace(session, workspace_sync, caller).model_dump(mode="json")
    _required(request.ticket_id, "ticket_id", request.action)
    if request.action is GithubIssueAction.STATUS:
        return _agent_status(_status(session, TicketIssueKey(ticket_id=request.ticket_id), caller))
    if request.action is GithubIssueAction.PUBLISH:
        key = TicketIssueKey(ticket_id=request.ticket_id)
        return _publish(session, key, caller).model_dump(mode="json")
    ticket_sync = TicketIssueSync(ticket_id=request.ticket_id, policy=request.policy)
    return _sync(session, ticket_sync, caller).model_dump(mode="json")


def _page_status(_session: Session, status: IssueLinkStatus) -> GithubIssueLinkView | None:
    return status.link


_PAGE_ONLY: Mapping[Transport, str] = {
    Transport.MCP: "agents reach it through loregarden_sync_github_issues, which runs "
    "github_issues with the matching action",
    Transport.CLI: "the CLI runs MCP's tools, which reach it through github_issues",
}

_STATUS_REPLIES: Mapping[Transport, Callable[[Session, Any], Any]] = {
    Transport.REST: _page_status,
}

GITHUB_ISSUE_OPERATIONS = (
    Operation(
        id=OperationId.GITHUB_ISSUES,
        effect=Effect.WRITE,
        args=GithubIssueRequest,
        handler=_dispatch,
        unavailable_on={
            Transport.REST: "the page calls each action's own operation, one route each",
        },
    ),
    Operation(
        id=OperationId.GITHUB_ISSUE_LINK,
        effect=Effect.READ,
        args=TicketIssueKey,
        handler=_status,
        unavailable_on=_PAGE_ONLY,
        replies=_STATUS_REPLIES,
    ),
    Operation(
        id=OperationId.GITHUB_ISSUE_PUBLISH,
        effect=Effect.WRITE,
        args=TicketIssueKey,
        handler=_publish,
        unavailable_on=_PAGE_ONLY,
    ),
    Operation(
        id=OperationId.GITHUB_ISSUE_SYNC,
        effect=Effect.WRITE,
        args=TicketIssueSync,
        handler=_sync,
        unavailable_on=_PAGE_ONLY,
    ),
    Operation(
        id=OperationId.GITHUB_ISSUES_SYNC_WORKSPACE,
        effect=Effect.WRITE,
        args=WorkspaceIssueSync,
        handler=_sync_workspace,
        unavailable_on=_PAGE_ONLY,
    ),
)
