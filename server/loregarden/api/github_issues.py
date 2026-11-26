"""Two-way sync between tickets and GitHub issues.

Per ticket: read the link, publish the ticket as a new issue, sync it, unlink it.
Per workspace: sync every link, and optionally import unlinked open issues under
a parent. And a webhook, so an edit on GitHub reaches the ticket without anyone
pressing sync — see `services.github_issue_sync` for how directions are chosen.
"""

import json

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response
from loregarden.api.github_signature import verify_github_signature
from loregarden.api.operation_inputs import OperationBody
from loregarden.api.operation_routes import run_rest_operation
from loregarden.db.session import get_session
from loregarden.models.domain import (
    GithubIssueLinkView,
    GithubSyncSettingsView,
    LinkSyncResult,
    Ticket,
    UpdateGithubSyncSettings,
    Workspace,
    WorkspaceSyncResult,
)
from loregarden.services.github_issue_sync import (
    IssueWebhookEvent,
    handle_issue_event,
    unlink_ticket,
)
from loregarden.services.github_sync_scheduler import get_sync_settings, update_sync_settings
from loregarden.services.operations import OperationId
from loregarden.services.organization_gate_service import (
    UnknownWorkspaceError,
    workspace_for_slug,
)
from pydantic import ValidationError
from sqlmodel import Session

router = APIRouter(tags=["github-issues"])


class SyncLinkRequest(
    OperationBody, operation=OperationId.GITHUB_ISSUE_SYNC, supplied={"ticket_id"}
):
    """How to settle a conflict between the ticket and its issue."""


class SyncWorkspaceRequest(
    OperationBody, operation=OperationId.GITHUB_ISSUES_SYNC_WORKSPACE, supplied={"workspace_slug"}
):
    """Where a workspace sync imports the open issues no ticket links."""


def _ticket(session: Session, ticket_id: str) -> Ticket:
    ticket = session.get(Ticket, ticket_id)
    if ticket is None:
        raise HTTPException(404, "Ticket not found")
    return ticket


@router.get("/tickets/{ticket_id}/github-issue", response_model=GithubIssueLinkView | None)
def get_ticket_issue_link(
    ticket_id: str, session: Session = Depends(get_session)
) -> GithubIssueLinkView | None:
    return run_rest_operation(session, OperationId.GITHUB_ISSUE_LINK, {"ticket_id": ticket_id})


@router.post("/tickets/{ticket_id}/github-issue", response_model=LinkSyncResult)
def publish_ticket_issue(ticket_id: str, session: Session = Depends(get_session)) -> LinkSyncResult:
    return run_rest_operation(session, OperationId.GITHUB_ISSUE_PUBLISH, {"ticket_id": ticket_id})


@router.post("/tickets/{ticket_id}/github-issue/sync", response_model=LinkSyncResult)
def sync_ticket_issue(
    ticket_id: str,
    body: SyncLinkRequest | None = None,
    session: Session = Depends(get_session),
) -> LinkSyncResult:
    request = body or SyncLinkRequest()
    return run_rest_operation(
        session, OperationId.GITHUB_ISSUE_SYNC, {**request.model_dump(), "ticket_id": ticket_id}
    )


@router.delete("/tickets/{ticket_id}/github-issue", status_code=204)
def unlink_ticket_issue(ticket_id: str, session: Session = Depends(get_session)) -> Response:
    _ticket(session, ticket_id)
    if not unlink_ticket(session, ticket_id):
        raise HTTPException(404, "Ticket is not linked to a GitHub issue")
    return Response(status_code=204)


@router.post("/workspaces/{workspace_slug}/github-issues/sync", response_model=WorkspaceSyncResult)
def sync_workspace_issues(
    workspace_slug: str,
    body: SyncWorkspaceRequest | None = None,
    session: Session = Depends(get_session),
) -> WorkspaceSyncResult:
    request = body or SyncWorkspaceRequest()
    return run_rest_operation(
        session,
        OperationId.GITHUB_ISSUES_SYNC_WORKSPACE,
        {"workspace_slug": workspace_slug, **request.model_dump()},
    )


def _workspace(session: Session, workspace_slug: str) -> Workspace:
    try:
        return workspace_for_slug(session, workspace_slug)
    except UnknownWorkspaceError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.get(
    "/workspaces/{workspace_slug}/github-issues/settings", response_model=GithubSyncSettingsView
)
def get_workspace_sync_settings(
    workspace_slug: str, session: Session = Depends(get_session)
) -> GithubSyncSettingsView:
    return get_sync_settings(session, _workspace(session, workspace_slug))


@router.put(
    "/workspaces/{workspace_slug}/github-issues/settings", response_model=GithubSyncSettingsView
)
def put_workspace_sync_settings(
    workspace_slug: str,
    body: UpdateGithubSyncSettings,
    session: Session = Depends(get_session),
) -> GithubSyncSettingsView:
    try:
        return update_sync_settings(session, _workspace(session, workspace_slug), body)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/github/issues/webhook/{workspace_id}")
async def receive_issue_webhook(
    workspace_id: str,
    request: Request,
    parent_ticket_id: str = Query(
        "", description="Import newly opened issues under this ticket. Blank: sync links only."
    ),
    x_github_event: str | None = Header(None),
    x_hub_signature_256: str | None = Header(None),
    session: Session = Depends(get_session),
) -> dict:
    """GitHub ``issues`` deliveries. Requires `LOREGARDEN_CI_WEBHOOK_SECRET`.

    Unlike the CI webhook, an unsigned delivery is refused even with no secret
    configured: this payload is written into tickets.
    """
    body = await request.body()
    if not verify_github_signature(body, x_hub_signature_256, require_secret=True):
        raise HTTPException(403, "Invalid or missing webhook signature")
    # py-org: allow-string — GitHub's event names.
    if x_github_event == "ping":
        return {"status": "ok"}
    # py-org: allow-string — GitHub's event names.
    if x_github_event != "issues":
        return {"status": "ignored", "reason": f"event {x_github_event!r} is not handled"}
    workspace = session.get(Workspace, workspace_id)
    if workspace is None:
        raise HTTPException(404, "Workspace not found")
    try:
        event = IssueWebhookEvent.model_validate(json.loads(body))
    except (ValueError, ValidationError) as exc:
        raise HTTPException(400, f"Not an issues payload: {exc}") from exc
    try:
        result = handle_issue_event(
            session, workspace, event, import_parent_ticket_id=parent_ticket_id
        )
    except (ValueError, LookupError) as exc:
        raise HTTPException(400, str(exc)) from exc
    if result is None:
        return {"status": "ignored", "reason": "issue is not linked to a ticket here"}
    if result.error:
        return {"status": "error", "result": result.model_dump(mode="json")}
    return {"status": "ok", "result": result.model_dump(mode="json")}
