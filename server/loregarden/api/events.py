import json

from fastapi import APIRouter, Depends
from loregarden.core.event_bus import event_bus
from loregarden.db.session import get_session
from loregarden.models.domain import EventView
from sqlmodel import Session

router = APIRouter(prefix="/events", tags=["events"])


@router.get("", response_model=list[EventView])
def list_events(
    limit: int = 100,
    session: Session = Depends(get_session),
) -> list[EventView]:
    events = event_bus.list_recent(session, limit=limit)
    return [
        EventView(
            id=e.id,
            type=e.type,
            ticket_id=e.ticket_id,
            workspace_id=e.workspace_id,
            payload=json.loads(e.payload_json or "{}"),
            created_at=e.created_at,
        )
        for e in events
    ]
