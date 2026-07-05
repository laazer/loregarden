from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session

from loregarden.db.session import get_session
from loregarden.models.domain import Ticket, TriageMessageCreate
from loregarden.services.triage_service import send_triage_message, triage_snapshot

router = APIRouter(prefix="/tickets", tags=["triage"])


@router.get("/{ticket_id}/triage")
def get_ticket_triage(ticket_id: str, session: Session = Depends(get_session)) -> dict:
    ticket = session.get(Ticket, ticket_id)
    if not ticket:
        raise HTTPException(404, "Ticket not found")
    return triage_snapshot(session, ticket)


@router.post("/{ticket_id}/triage/messages")
def post_triage_message(
    ticket_id: str,
    body: TriageMessageCreate,
    session: Session = Depends(get_session),
) -> dict:
    ticket = session.get(Ticket, ticket_id)
    if not ticket:
        raise HTTPException(404, "Ticket not found")
    try:
        return send_triage_message(session, ticket, body.content)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
