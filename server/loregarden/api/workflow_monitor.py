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
    """Persisted findings, plus workspace-scoped conditions recomputed now.

    Omit `ticket_id` for every current finding — which is how a person scanning
    for problems finds one, since a finding is *about* a ticket they have no
    reason to have opened yet.

    An empty `ticket_id` is normalised to None rather than passed through.
    `list_findings` branches on `if ticket_id:` for the row filter and on
    `if ticket_id is None:` for the workspace-scoped half, so `?ticket_id=`
    returned every ticket's persisted findings while silently dropping the
    recomputed ones — a half-answer shaped exactly like a whole one. The client
    built that URL whenever its ticket id was empty.
    """
    return [
        item.model_dump(mode="json") for item in list_findings(session, ticket_id=ticket_id or None)
    ]


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
