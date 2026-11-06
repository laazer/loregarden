"""What a landing ticket is waiting on before it is marked done.

Its own router rather than another route in `tickets.py`, which is a hotspot.
A plain `def`: it reads SQLite, and FastAPI runs sync handlers in its
threadpool, so the event loop never waits on the database.
"""

from fastapi import APIRouter, Depends, HTTPException
from loregarden.db.session import get_session
from loregarden.models.domain import Ticket
from loregarden.services.landing_status import TicketLandingStatus, ticket_landing_status
from loregarden.services.ticket_ids import resolve as resolve_external_id
from loregarden.services.ticket_workspace_binding import workspace_for_ticket
from sqlmodel import Session

router = APIRouter(prefix="/tickets", tags=["tickets"])


@router.get("/{ticket_id}/landing", response_model=TicketLandingStatus)
def get_ticket_landing(
    ticket_id: str, session: Session = Depends(get_session)
) -> TicketLandingStatus:
    ticket = session.get(Ticket, ticket_id) or resolve_external_id(session, ticket_id)
    if not ticket:
        raise HTTPException(404, "Ticket not found")
    workspace = workspace_for_ticket(session, ticket)
    if not workspace:
        raise HTTPException(404, "Workspace not found")
    return ticket_landing_status(session, ticket, workspace)
