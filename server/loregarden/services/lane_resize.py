"""Change how many lanes run at once, without stranding what is in them.

Growing is simple: the pool gains slots and their lanes start empty. Shrinking
has three cases, and each must leave nothing behind:

- an idle lane past the new count is retired at once;
- a lane with work in it keeps running until that work ends, then retires as it
  frees (`ParallelQueueService.initialize_slots` and the release paths);
- entries waiting in a retired lane move to the tails of the remaining lanes.

Kept out of `queue_lanes`, which is past a thousand lines; this composes its
public pieces rather than adding another responsibility to it.
"""

from __future__ import annotations

from dataclasses import dataclass

from loregarden.models.domain import AgentSlot
from loregarden.services.lane_count import store_lane_count
from loregarden.services.queue_lanes import QueueLaneService
from loregarden.websocket_events import emit_execution_update
from sqlmodel import Session, select


@dataclass(frozen=True)
class LaneResize:
    lane_count: int
    #: Lanes past the new count still finishing work; they retire as they free.
    retiring_lanes: list[int]
    #: Entries moved out of retired lanes into the remaining ones.
    moved_entries: int


def resize_lanes(session: Session, count: int) -> LaneResize:
    """Set the lane count and fit the pool to it. Raises ValueError out of range."""
    store_lane_count(session, count)
    lanes = QueueLaneService(session, max_concurrent=count)
    lanes.slots.initialize_slots()
    moved = lanes.rehome_retired_lane_entries()
    if moved:
        # Moved entries may have landed in an idle lane; start them now rather
        # than on the next reconcile pass.
        for slot in session.exec(
            select(AgentSlot).where(AgentSlot.is_available == True)  # noqa: E712
        ).all():
            if lanes.waiting_in_lane(slot.slot_number):
                lanes.start_lane_head(slot.slot_number)
    retiring = sorted(
        session.exec(select(AgentSlot.slot_number).where(AgentSlot.slot_number > count)).all()
    )
    emit_execution_update()
    return LaneResize(lane_count=count, retiring_lanes=retiring, moved_entries=moved)
