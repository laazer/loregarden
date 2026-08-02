"""HTTP surface for the per-slot execution lanes.

Kept apart from `parallel.py`, which is about runs and worktrees. What a lane
exposes is a small, closed set: put a ticket in one, take a waiting one out,
move one around. Starting is not among them — adding to an idle lane starts it,
which is the point of the model.
"""

import logging

from fastapi import APIRouter, Body, Depends, HTTPException, Path
from loregarden.db.session import get_session
from loregarden.services.queue_lanes import QueueLaneService
from pydantic import BaseModel, Field
from sqlmodel import Session

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/parallel/lanes", tags=["queue-lanes"])


class AddToLaneRequest(BaseModel):
    ticket_id: str
    #: Honoured whenever the lane reaches this entry, not when it is added.
    auto_approve: bool = False
    stop_at_stage_key: str = ""


class MoveEntryRequest(BaseModel):
    slot_number: int = Field(ge=1)
    position: int = Field(ge=1)


@router.post("/{slot_number}/entries")
def add_to_lane(
    slot_number: int = Path(..., ge=1),
    body: AddToLaneRequest = Body(...),
    session: Session = Depends(get_session),
):
    """Put a ticket in a lane. Starts it when the lane is idle, queues it otherwise."""
    try:
        return QueueLaneService(session).add_to_lane(
            ticket_id=body.ticket_id,
            slot_number=slot_number,
            auto_approve=body.auto_approve,
            stop_at_stage_key=body.stop_at_stage_key or None,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.error("Error adding to lane: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.delete("/entries/{entry_id}")
def remove_lane_entry(
    entry_id: str = Path(...),
    session: Session = Depends(get_session),
):
    """Take a waiting entry out of its lane. A running entry is not removable here."""
    removed = QueueLaneService(session).remove_entry(entry_id)
    if not removed:
        raise HTTPException(status_code=404, detail="No waiting lane entry with that id")
    return {"status": "removed", "entry_id": entry_id}


@router.post("/entries/{entry_id}/move")
def move_lane_entry(
    entry_id: str = Path(...),
    body: MoveEntryRequest = Body(...),
    session: Session = Depends(get_session),
):
    """Reorder a waiting entry within its lane, or move it to another one."""
    moved = QueueLaneService(session).move_entry(
        entry_id, slot_number=body.slot_number, position=body.position
    )
    if not moved:
        raise HTTPException(status_code=404, detail="No waiting lane entry with that id")
    return {"status": "moved", "entry_id": entry_id, "slot_number": body.slot_number}
