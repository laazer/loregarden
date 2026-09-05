import json

from fastapi import APIRouter, Depends, HTTPException
from loregarden.core.event_bus import event_bus
from loregarden.db.session import get_session
from loregarden.models.domain import EventType, EventView, Ticket
from loregarden.services.ticket_ids import resolve as resolve_external_id
from sqlmodel import Session

router = APIRouter(prefix="/events", tags=["events"])


def _view(event) -> EventView:
    return EventView(
        id=event.id,
        type=event.type,
        ticket_id=event.ticket_id,
        workspace_id=event.workspace_id,
        payload=json.loads(event.payload_json or "{}"),
        created_at=event.created_at,
    )


@router.get("", response_model=list[EventView])
def list_events(
    limit: int = 100,
    ticket_id: str | None = None,
    workspace_id: str | None = None,
    event_type: EventType | None = None,
    session: Session = Depends(get_session),
) -> list[EventView]:
    """Newest first. Filters compose; unfiltered this is installation-wide."""
    events = event_bus.list_recent(
        session,
        limit=limit,
        ticket_id=ticket_id,
        workspace_id=workspace_id,
        types=(event_type,) if event_type else None,
    )
    return [_view(event) for event in events]


@router.get("/ticket/{ticket_id}/history", response_model=list[EventView])
def ticket_history(
    ticket_id: str, limit: int = 100, session: Session = Depends(get_session)
) -> list[EventView]:
    """How one ticket got to its current state, oldest first.

    The event log is the only place this exists: `tickets.state` holds the
    current value and `stages_json` is overwritten in place, so without these
    rows there is no record of any transition that ever happened.
    """
    # resolve() returns the Ticket, not its id — accepting any spelling of the
    # external id, which is what a shared link carries after a re-parent.
    ticket = resolve_external_id(session, ticket_id) or session.get(Ticket, ticket_id)
    if ticket is None:
        raise HTTPException(status_code=404, detail=f"Ticket not found: {ticket_id}")
    return [_view(event) for event in event_bus.ticket_history(session, ticket.id, limit=limit)]
