"""A ticket's pull request, live from GitHub (the PR tab's data).

Its own router rather than another route in `tickets.py`, which is a hotspot.
A plain `def`: the lookup shells out to `gh`, and FastAPI runs sync handlers in
its threadpool, so a slow GitHub cannot stall the event loop.
"""

from fastapi import APIRouter, Depends, HTTPException
from loregarden.db.session import get_session
from loregarden.models.domain import Ticket, Workspace
from loregarden.services.ticket_ids import resolve as resolve_external_id
from loregarden.services.ticket_pull_request import TicketPullRequest, ticket_pull_request
from sqlmodel import Session

router = APIRouter(prefix="/tickets", tags=["tickets"])


@router.get("/{ticket_id}/pull-request", response_model=TicketPullRequest)
def get_ticket_pull_request(
    ticket_id: str, session: Session = Depends(get_session)
) -> TicketPullRequest:
    ticket = session.get(Ticket, ticket_id) or resolve_external_id(session, ticket_id)
    if not ticket:
        raise HTTPException(404, "Ticket not found")
    workspace = session.get(Workspace, ticket.workspace_id)
    if not workspace:
        raise HTTPException(404, "Workspace not found")
    return ticket_pull_request(session, ticket, workspace)
