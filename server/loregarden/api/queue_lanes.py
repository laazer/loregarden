"""HTTP surface for the per-slot execution lanes.

Kept apart from `parallel.py`, which is about runs and worktrees. What a lane
exposes is a small, closed set: put a ticket in one, take a waiting one out,
move one around. Starting is not among them — adding to an idle lane starts it,
which is the point of the model.
"""

import logging
from dataclasses import asdict

from fastapi import APIRouter, Body, Depends, HTTPException, Path, Query
from loregarden.db.session import get_session
from loregarden.services.lane_count import MAX_LANE_COUNT, MIN_LANE_COUNT, lane_count
from loregarden.services.lane_resize import resize_lanes
from loregarden.services.queue_history import OUTCOMES, QueueHistoryService
from loregarden.services.queue_lanes import QueueLaneService
from loregarden.websocket_events import emit_execution_update
from pydantic import BaseModel, Field
from sqlmodel import Session

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/parallel/lanes", tags=["queue-lanes"])


class AddToLaneRequest(BaseModel):
    ticket_id: str
    #: Honoured whenever the lane reaches this entry, not when it is added.
    auto_approve: bool = False
    approve_design_plans: bool = True
    auto_repair: bool = True
    stop_at_stage_key: str = ""
    #: Max seconds each agent run for this ticket (and its children) may take.
    timeout_seconds: int | None = Field(default=None, ge=30)


class LaneCountRequest(BaseModel):
    lane_count: int = Field(ge=MIN_LANE_COUNT, le=MAX_LANE_COUNT)


class MoveEntryRequest(BaseModel):
    slot_number: int = Field(ge=1)
    position: int = Field(ge=1)


@router.get("/count")
def get_lane_count(session: Session = Depends(get_session)):
    """How many lanes run at once, and the range it may be set within."""
    return {
        "lane_count": lane_count(session),
        "min": MIN_LANE_COUNT,
        "max": MAX_LANE_COUNT,
    }


@router.put("/count")
def set_lane_count(
    body: LaneCountRequest = Body(...),
    session: Session = Depends(get_session),
):
    """Change the lane count. Lowering it never interrupts running work.

    Idle lanes past the new count go at once; busy ones finish first. Entries
    waiting in a retired lane move to the remaining lanes.
    """
    result = resize_lanes(session, body.lane_count)
    return {
        "lane_count": result.lane_count,
        "retiring_lanes": result.retiring_lanes,
        "moved_entries": result.moved_entries,
    }


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
            approve_design_plans=body.approve_design_plans,
            auto_repair=body.auto_repair,
            stop_at_stage_key=body.stop_at_stage_key or None,
            timeout_seconds=body.timeout_seconds,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.error("Error adding to lane: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/history")
def get_lane_history(
    workspace_id: str = Query(default=""),
    outcome: str = Query(default=""),
    slot_number: int | None = Query(default=None, ge=1),
    ticket_id: str = Query(default=""),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    session: Session = Depends(get_session),
):
    """Lane entries that already ran, newest first.

    The board only ever shows what is live, so a ticket that blocked mid-pipeline
    used to vanish with it. `outcome` is derived from the orchestration run, not
    from the entry's own status — see `queue_history`.
    """
    if outcome and outcome not in OUTCOMES:
        raise HTTPException(
            status_code=400, detail=f"Unknown outcome '{outcome}'; expected one of {OUTCOMES}"
        )
    history = QueueHistoryService(session)
    entries, total = history.list_history(
        workspace_id=workspace_id,
        outcome=outcome,
        slot_number=slot_number,
        ticket_id=ticket_id,
        limit=limit,
        offset=offset,
    )
    return {
        "entries": [asdict(entry) for entry in entries],
        "total": total,
        "limit": limit,
        "offset": offset,
        "cleared_at": history.last_cleared_at(),
    }


@router.post("/history/clear")
def clear_lane_history(session: Session = Depends(get_session)):
    """Hide everything that has finished so far from the history rail.

    Nothing is deleted — see `QueueHistoryService.clear_history`. A lane's
    needs-attention cards are not touched; those clear per lane.
    """
    return {"status": "cleared", "cleared_at": QueueHistoryService(session).clear_history()}


@router.post("/{slot_number}/attention/dismiss")
def dismiss_lane_attention(
    slot_number: int = Path(..., ge=1),
    session: Session = Depends(get_session),
):
    """Acknowledge every blocked/failed card a lane is holding at once."""
    dismissed = QueueHistoryService(session).dismiss_lane(slot_number)
    if dismissed:
        emit_execution_update()
    return {"status": "dismissed", "slot_number": slot_number, "dismissed": dismissed}


@router.post("/entries/{entry_id}/dismiss")
def dismiss_lane_entry(
    entry_id: str = Path(...),
    session: Session = Depends(get_session),
):
    """Acknowledge a blocked/failed entry so its lane card stops holding it.

    Nothing is deleted — the entry stays in history. This only clears it from
    the lane's needs-attention section, which is the part that must not empty
    itself while an operator has not looked at it.
    """
    if not QueueHistoryService(session).dismiss_entry(entry_id):
        raise HTTPException(status_code=404, detail="No finished lane entry with that id")
    emit_execution_update()
    return {"status": "dismissed", "entry_id": entry_id}


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
