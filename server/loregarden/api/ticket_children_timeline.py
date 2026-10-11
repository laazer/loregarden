"""A parent ticket's timeline: its direct children as rows of stage visits.

Its own router rather than another route in `tickets.py`, which is a hotspot.
"""

from fastapi import APIRouter, Depends, HTTPException
from loregarden.db.session import get_session
from loregarden.models.domain import Ticket
from loregarden.services.children_timeline import ChildrenTimeline, children_timeline
from sqlmodel import Session

router = APIRouter(prefix="/tickets", tags=["tickets"])


@router.get("/{ticket_id}/children-timeline", response_model=ChildrenTimeline)
def get_children_timeline(
    ticket_id: str, session: Session = Depends(get_session)
) -> ChildrenTimeline:
    """Each direct child's stage visits, where it is now, and what it waits on.

    A child with children of its own reports ``child_count``; the reader
    expands it by requesting that child's timeline.
    """
    ticket = session.get(Ticket, ticket_id)
    if not ticket:
        raise HTTPException(404, "Ticket not found")
    return children_timeline(session, ticket)
