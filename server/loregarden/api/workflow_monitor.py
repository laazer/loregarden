"""Read the workflow monitor's findings. Report-only: nothing here mutates."""

from fastapi import APIRouter, Depends
from loregarden.db.session import get_session
from loregarden.services.workflow_monitor import list_findings, scan
from sqlmodel import Session

router = APIRouter(prefix="/monitor", tags=["monitor"])


@router.get("/findings")
def monitor_findings(
    ticket_id: str | None = None, session: Session = Depends(get_session)
) -> list[dict]:
    """Persisted findings, plus workspace-scoped conditions recomputed now."""
    return [item.model_dump(mode="json") for item in list_findings(session, ticket_id=ticket_id)]


@router.get("/scan")
def monitor_scan(
    ticket_id: str | None = None, session: Session = Depends(get_session)
) -> list[dict]:
    """Run the detectors now without persisting, for asking "what would it say?".

    Separate from /findings because the sweep runs on the reconcile timer: a
    reader wanting the current answer should not have to wait for the next tick,
    and should not trigger a write by asking.
    """
    return [item.model_dump(mode="json") for item in scan(session, ticket_id=ticket_id)]
