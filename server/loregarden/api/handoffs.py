"""The handoffs agents passed each other, for the Baxter dock's Handoffs tab.

Its own router: `tickets.py` is a hotspot, and the feed spans tickets. A plain
`def`, so FastAPI runs the SQLite read in its threadpool.
"""

from fastapi import APIRouter, Depends
from loregarden.api.operation_routes import run_rest_operation
from loregarden.db.session import get_session
from loregarden.services.handoff_feed import HandoffFeed
from loregarden.services.operations import OperationId
from sqlmodel import Session

router = APIRouter(prefix="/handoffs", tags=["handoffs"])


@router.get("", response_model=HandoffFeed)
def get_handoffs(
    ticket_id: str | None = None,
    workspace: str | None = None,
    limit: int = 50,
    session: Session = Depends(get_session),
) -> HandoffFeed:
    """Newest first: one ticket's handoffs, one workspace's, or every workspace's."""
    return run_rest_operation(
        session,
        OperationId.HANDOFF_FEED,
        {"ticket_id": ticket_id, "workspace": workspace, "limit": limit},
    )
