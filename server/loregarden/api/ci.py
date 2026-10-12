"""CI integration endpoints: status, operator actions, and the GitHub webhook.

The poller (`services.ci_poll_scheduler`) is the CI loop's input. The webhook
is an optional nudge for a server GitHub can reach: a signed `workflow_run`
delivery polls that workspace now instead of at the next tick, through the
same `CIService.observe`. Only GitHub is supported. Any other delivery is
refused with a 400 rather than accepted and dropped.
"""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from loregarden.api.github_signature import verify_github_signature
from loregarden.config import settings
from loregarden.db.session import engine, get_session
from loregarden.models.domain import Ticket, Workspace
from loregarden.services.ci_poll_scheduler import WorkspacePoll, poll_workspace
from loregarden.services.ci_service import CIService
from sqlmodel import Session, or_, select

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/ci", tags=["ci"])

#: The one GitHub event that reports a CI run finishing.
_WORKFLOW_RUN_EVENT = "workflow_run"  # py-org: allow-string


def _poll_now(workspace_ref: str) -> WorkspacePoll | None:
    with Session(engine) as session:
        workspace = session.exec(
            select(Workspace).where(
                or_(Workspace.id == workspace_ref, Workspace.slug == workspace_ref)
            )
        ).first()
        if workspace is None:
            return None
        return poll_workspace(session, workspace)


@router.post("/webhook/{workspace_id}")
async def receive_ci_webhook(
    workspace_id: str,
    request: Request,
    x_github_event: str | None = Header(None),
    x_hub_signature_256: str | None = Header(None),
):
    """A GitHub `workflow_run` delivery: poll ``workspace_id`` (id or slug) now."""
    body = await request.body()
    if not x_github_event:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Unsupported CI provider: only GitHub webhook deliveries are accepted",
        )
    if not verify_github_signature(body, x_hub_signature_256):
        logger.warning("GitHub webhook signature verification failed")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Invalid webhook signature"
        )
    if x_github_event != _WORKFLOW_RUN_EVENT:
        return {"status": "ignored", "reason": f"Event type not supported: {x_github_event}"}
    if not settings.ci_enabled:
        return {"status": "disabled", "reason": "CI integration is off (ci_enabled)"}

    poll = await asyncio.to_thread(_poll_now, workspace_id)
    if poll is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Workspace not found")
    return {
        "status": "error" if poll.errors else "ok",
        "observed": poll.observed,
        "dispatched": poll.dispatched,
        "errors": poll.errors,
    }


@router.get("/status/{ticket_id}")
def get_ci_status(ticket_id: str, session: Session = Depends(get_session)):
    """The ticket's latest CI result and every auto-fix attempt on it, oldest first."""
    service = CIService(session)
    result = service.latest_result(ticket_id)
    return {
        "ci_status": result.model_dump(mode="json") if result else None,
        "auto_fix_history": [
            attempt.model_dump(mode="json") for attempt in service.attempts(ticket_id)
        ],
    }


@router.post("/manual-override/{ticket_id}")
def skip_ci_check(ticket_id: str, session: Session = Depends(get_session)):
    """Mark the latest CI result skipped: a person decided it does not gate this commit."""
    result = CIService(session).skip_ci_check(ticket_id)
    if result is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No CI result for ticket")
    return {"status": "ok", "message": f"CI check skipped for ticket {ticket_id}"}


@router.post("/trigger-auto-fix/{ticket_id}")
def trigger_manual_auto_fix(ticket_id: str, session: Session = Depends(get_session)):
    """Reopen the ticket for a CI fix turn now, or say why it cannot be."""
    ticket = session.get(Ticket, ticket_id)
    if ticket is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Ticket not found")
    outcome = CIService(session).trigger_latest(ticket)
    if not outcome.dispatched or outcome.attempt is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=f"Auto-fix not started: {outcome.detail}"
        )
    return {
        "status": "ok",
        "attempt_id": outcome.attempt.id,
        "attempt_number": outcome.attempt.attempt_number,
        "message": f"Reopened for CI fix attempt {outcome.attempt.attempt_number}",
    }
