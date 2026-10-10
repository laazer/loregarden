"""The `loregarden_sync_github_issues` MCP tool: schema and handler.

Its own module, registered in `tool_registry.EXTENDED_TOOLS`, so the
`execute_tool` chain does not grow. The work is `services.github_issue_sync`;
this parses arguments and serialises results.
"""

from __future__ import annotations

import json
from enum import StrEnum
from typing import Any

from pydantic import ConfigDict, ValidationError
from sqlmodel import Session, SQLModel

from loregarden.mcp.tool_ids import McpTool
from loregarden.models.domain import (
    ConflictPolicy,
    GithubIssueLinkView,
    Ticket,
    WorkItemType,
)
from loregarden.services.github_issue_sync import (
    link_for_ticket,
    publish_ticket,
    sync_link,
    sync_workspace,
)
from loregarden.services.organization_gate_service import workspace_for_slug


class GithubIssueAction(StrEnum):
    #: Read a ticket's link. Touches nothing.
    STATUS = "status"
    #: Open an issue for an unlinked ticket and link them.
    PUBLISH = "publish"
    #: Sync one linked ticket with its issue.
    SYNC = "sync"
    #: Sync every link in a workspace; import unlinked open issues when a parent is given.
    SYNC_WORKSPACE = "sync_workspace"


TOOL_DEFINITION: dict[str, Any] = {
    "name": McpTool.SYNC_GITHUB_ISSUES,
    "description": (
        "Two-way sync between tickets and GitHub issues (title, body/description, "
        "and open/closed — done is 'closed as completed', wont_do is 'closed as not "
        "planned'). Each field moves in whichever direction changed since the last "
        "sync; a field changed on both sides is reported as a conflict and left alone "
        "unless policy is local or remote. Actions: status (read a ticket's link), "
        "publish (open an issue for a ticket), sync (one ticket), sync_workspace (every "
        "linked ticket; with import_parent_ticket_id, also import unlinked open issues "
        "as children of that ticket). Uses the gh CLI's credentials."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": [a.value for a in GithubIssueAction],
                "description": "What to do.",
            },
            "ticket_id": {
                "type": "string",
                "description": "Ticket id — for status, publish and sync.",
            },
            "workspace_slug": {
                "type": "string",
                "description": "Workspace slug — for sync_workspace.",
            },
            "policy": {
                "type": "string",
                "enum": [p.value for p in ConflictPolicy],
                "description": "Conflicting fields: report (default), local wins, remote wins.",
            },
            "import_parent_ticket_id": {
                "type": "string",
                "description": "sync_workspace: import unlinked open issues under this ticket.",
            },
            "import_work_item_type": {
                "type": "string",
                "enum": [t.value for t in WorkItemType if t is not WorkItemType.INITIATIVE],
                "description": "sync_workspace: type for imported tickets (default bug).",
            },
            "import_label": {
                "type": "string",
                "description": "sync_workspace: only import issues with this label.",
            },
        },
        "required": ["action"],
        "additionalProperties": False,
    },
}


class GithubIssueRequest(SQLModel):
    model_config = ConfigDict(extra="forbid")

    action: GithubIssueAction
    ticket_id: str = ""
    workspace_slug: str = ""
    policy: ConflictPolicy = ConflictPolicy.REPORT
    import_parent_ticket_id: str = ""
    import_work_item_type: WorkItemType = WorkItemType.BUG
    import_label: str = ""


def _ticket(session: Session, request: GithubIssueRequest) -> Ticket:
    if not request.ticket_id:
        raise ValueError(f"ticket_id is required for action {request.action.value}")
    ticket = session.get(Ticket, request.ticket_id)
    if ticket is None:
        raise ValueError(f"no ticket with id {request.ticket_id!r}")
    return ticket


def _status(session: Session, request: GithubIssueRequest) -> dict[str, Any]:
    ticket = _ticket(session, request)
    link = link_for_ticket(session, ticket.id)
    view = GithubIssueLinkView.model_validate(link, from_attributes=True) if link else None
    return {
        "ticket_id": ticket.id,
        "linked": view is not None,
        "link": view.model_dump(mode="json") if view else None,
    }


def _publish(session: Session, request: GithubIssueRequest) -> dict[str, Any]:
    return publish_ticket(session, _ticket(session, request)).model_dump(mode="json")


def _sync(session: Session, request: GithubIssueRequest) -> dict[str, Any]:
    ticket = _ticket(session, request)
    link = link_for_ticket(session, ticket.id)
    if link is None:
        raise ValueError(f"{ticket.external_id} is not linked to a GitHub issue; publish it first")
    return sync_link(session, link, policy=request.policy).model_dump(mode="json")


def _sync_workspace(session: Session, request: GithubIssueRequest) -> dict[str, Any]:
    if not request.workspace_slug:
        raise ValueError("workspace_slug is required for action sync_workspace")
    workspace = workspace_for_slug(session, request.workspace_slug)
    return sync_workspace(
        session,
        workspace,
        import_parent_ticket_id=request.import_parent_ticket_id,
        import_work_item_type=request.import_work_item_type,
        import_label=request.import_label,
        policy=request.policy,
    ).model_dump(mode="json")


_ACTIONS = {
    GithubIssueAction.STATUS: _status,
    GithubIssueAction.PUBLISH: _publish,
    GithubIssueAction.SYNC: _sync,
    GithubIssueAction.SYNC_WORKSPACE: _sync_workspace,
}


def sync_github_issues(session: Session, arguments: dict[str, Any]) -> str:
    try:
        request = GithubIssueRequest.model_validate(arguments)
    except ValidationError as exc:
        raise ValueError(
            f"invalid arguments for {McpTool.SYNC_GITHUB_ISSUES.value}: {exc}"
        ) from exc
    return json.dumps(_ACTIONS[request.action](session, request), indent=2)
