"""A driven ticket stops at a stage boundary so the server can reload.

`reload_hold` decides when; this puts the ticket back at the front of its lane on
the terms its run had, so it carries on from the stage it stopped before. Only
the ticket at the top of the run is queued: a child is re-run by the parent that
was running it.
"""

from __future__ import annotations

from dataclasses import dataclass

from loregarden.models.domain import (
    AgentSlot,
    OrchestrationRun,
    QueuedRun,
    QueueEntryKind,
    QueuePosition,
    Ticket,
)
from loregarden.services.queue_lanes import QueueLaneService, least_busy_lane
from sqlmodel import Session, select

#: Why the run ended, and how a parent knows its child stopped for a reload
#: rather than for something a person has to see.
RELOAD_YIELD_MESSAGE = "Paused for a server reload; the ticket is queued to continue"


@dataclass(frozen=True)
class Continuation:
    """What a run that stops here owes its ticket."""

    #: A parent's run is driving this one, and re-runs it.
    nested: bool
    #: The caller's stage limit, less what has run. None is the profile's.
    max_stages: int | None

    @classmethod
    def of(cls, *, nested: bool, max_stages: int | None, stages_run: int) -> Continuation:
        # 0 means no limit, so a limit that is used up must not become one.
        left = max(1, max_stages - stages_run) if max_stages else max_stages
        return cls(nested=nested, max_stages=left)


def yielded_to_reload(run: OrchestrationRun) -> bool:
    return run.error_message == RELOAD_YIELD_MESSAGE


def queue_continuation(
    session: Session, orch_run: OrchestrationRun, ticket: Ticket, continuation: Continuation
) -> QueuedRun | None:
    """Queue ``ticket`` first in its lane, to run on the terms ``orch_run`` ran on."""
    if continuation.nested:
        return None
    lanes = QueueLaneService(session)
    slot = session.exec(
        select(AgentSlot).where(AgentSlot.current_orchestration_run_id == orch_run.id)
    ).first()
    lane = slot.slot_number if slot is not None else least_busy_lane(lanes)
    entry = QueuedRun(
        workspace_id=ticket.workspace_id,
        ticket_id=ticket.id,
        slot_number=lane,
        # Waiting entries are numbered from 1, and `renumber` closes the gap.
        position=0,
        status=QueuePosition.QUEUED,
        entry_kind=QueueEntryKind.ORCHESTRATION,
        auto_approve=orch_run.auto_approve,
        approve_design_plans=orch_run.approve_design_plans,
        auto_repair=orch_run.auto_repair,
        stop_at_stage_key=orch_run.stop_at_stage_key,
        driver=orch_run.driver.value,
        max_stages=continuation.max_stages,
        timeout_seconds=orch_run.timeout_override_seconds,
    )
    session.add(entry)
    session.commit()
    lanes.renumber(lane)
    return entry
