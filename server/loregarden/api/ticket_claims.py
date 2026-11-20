"""Which open sibling tickets claim the same files as this one.

Its own router rather than another route in `tickets.py`, which is a hotspot.
A plain `def`: it reads SQLite, and FastAPI runs sync handlers in its
threadpool, so the event loop never waits on the database.
"""

from fastapi import APIRouter, Depends
from loregarden.api.operation_routes import run_rest_operation
from loregarden.db.session import get_session
from loregarden.services.operations import OperationId
from loregarden.services.sibling_claims_view import SiblingClaimsView
from sqlmodel import Session

router = APIRouter(prefix="/tickets", tags=["tickets"])


@router.get("/{ticket_id}/sibling_claims", response_model=SiblingClaimsView)
def get_sibling_claims(
    ticket_id: str, session: Session = Depends(get_session)
) -> SiblingClaimsView:
    return run_rest_operation(session, OperationId.TICKET_SIBLING_CLAIMS, {"ticket_id": ticket_id})
