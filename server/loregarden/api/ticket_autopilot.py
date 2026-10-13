"""The autopilot of a milestone or parent ticket.

An initiative's lives on its plan page (`api.initiatives`); this is the same
engine rooted at any parent, read and switched from the ticket's own pane.
"""

from fastapi import APIRouter, Depends, HTTPException
from loregarden.db.session import get_session
from loregarden.models.domain import AutopilotUpdate, SubtreeAutopilotView
from loregarden.services.initiative_autopilot import (
    AutopilotRefusedError,
    run_autopilot,
    set_autopilot,
)
from loregarden.services.subtree_autopilot import subtree_autopilot_view
from sqlmodel import Session

router = APIRouter(prefix="/tickets", tags=["tickets"])

_OPERATOR = "human"


@router.get("/{ticket_id}/autopilot", response_model=SubtreeAutopilotView)
def get_ticket_autopilot(
    ticket_id: str, session: Session = Depends(get_session)
) -> SubtreeAutopilotView:
    try:
        return subtree_autopilot_view(session, ticket_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.patch("/{ticket_id}/autopilot", response_model=SubtreeAutopilotView)
def update_ticket_autopilot(
    ticket_id: str, body: AutopilotUpdate, session: Session = Depends(get_session)
) -> SubtreeAutopilotView:
    """Turning it on queues the first batch now rather than at the next tick."""
    try:
        plan = set_autopilot(session, ticket_id, body, actor=_OPERATOR)
        if plan.autopilot:
            run_autopilot(session, ticket_id)
        return subtree_autopilot_view(session, ticket_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except AutopilotRefusedError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
