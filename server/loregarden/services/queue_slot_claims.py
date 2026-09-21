"""Slot claims the lanes did not make: nested children and orphans.

Two reconciliation passes that read and write `agent_slots` directly rather
than through a lane. `release_nested_slot_claims` undoes the orphan-heal
binding free capacity to a child that already runs under its ancestor's lane;
`claim_orphaned_orchestrations` attaches free capacity to live orchestrations
that never claimed a slot. Split out of `queue_lanes` by size; the lane
service calls both from `reconcile_lanes`, in that order, after draining.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime, timezone

from loregarden.models.domain import AgentSlot, OrchestrationRun, QueuedRun, Ticket
from loregarden.services.parallel_queue import (
    LIVE_ORCHESTRATION_STATUSES,
    ParallelQueueService,
    tickets_holding_lanes,
)
from sqlmodel import Session, col, select

logger = logging.getLogger(__name__)


def _ticket_covered_by_ancestor_slot(
    session: Session, ticket_id: str, held_orchestration_ids: set[str]
) -> bool:
    """True when a live ancestor orchestration already holds a lane.

    BuiltinOrchestrator walks incomplete children with nested execute and
    opens a fresh OrchestrationRun per child. Those runs are intentional
    work under the parent's slot, not separate admissions.
    """
    ticket = session.get(Ticket, ticket_id)
    seen: set[str] = set()
    while ticket and ticket.parent_ticket_id:
        parent_id = ticket.parent_ticket_id
        if parent_id in seen:
            break
        seen.add(parent_id)
        parent_live = session.exec(
            select(OrchestrationRun)
            .where(OrchestrationRun.ticket_id == parent_id)
            .where(col(OrchestrationRun.status).in_(LIVE_ORCHESTRATION_STATUSES))
        ).all()
        if any(run.id in held_orchestration_ids for run in parent_live):
            return True
        ticket = session.get(Ticket, parent_id)
    return False


def release_nested_slot_claims(pool: ParallelQueueService) -> list[int]:
    """Free slots bound to nested children when an ancestor already holds one."""
    pool.initialize_slots()
    session = pool.session
    slots = list(session.exec(select(AgentSlot)).all())
    held = {
        slot.current_orchestration_run_id for slot in slots if slot.current_orchestration_run_id
    }
    freed: list[int] = []
    for slot in slots:
        orch_id = slot.current_orchestration_run_id
        if not orch_id:
            continue
        orch = session.get(OrchestrationRun, orch_id)
        if not orch:
            continue
        if not _ticket_covered_by_ancestor_slot(session, orch.ticket_id, held - {orch_id}):
            continue
        slot.is_available = True
        slot.current_orchestration_run_id = None
        slot.current_run_id = None
        slot.assigned_at = None
        session.add(slot)
        freed.append(slot.slot_number)
        logger.info(
            "Released nested slot %d claim for orchestration %s (ticket %s)",
            slot.slot_number,
            orch.id,
            orch.ticket_id,
        )
    if freed:
        session.commit()
    return freed


def claim_orphaned_orchestrations(
    pool: ParallelQueueService, *, ensure_entry: Callable[[int, str], QueuedRun | None]
) -> list[int]:
    """Bind free slots to live orchestrations that never claimed one.

    Admission used to be missing from a few start paths, so agents ran while
    every lane read Available. Healing on reconcile makes the board honest
    without waiting for those runs to finish and restart through the gate.

    Nested child orchestrations under a slotted ancestor are skipped — they
    share the ancestor's lane and must not consume the rest of the pool.

    `ensure_entry` is the lane service's `ensure_active_entry_for_orchestration`,
    passed in rather than imported: this module sits below `queue_lanes`.
    """
    pool.initialize_slots()
    session = pool.session
    held = {
        slot.current_orchestration_run_id
        for slot in session.exec(select(AgentSlot)).all()
        if slot.current_orchestration_run_id
    }
    lane_holders = tickets_holding_lanes(session)
    orphans = [
        run
        for run in session.exec(
            select(OrchestrationRun)
            .where(col(OrchestrationRun.status).in_(LIVE_ORCHESTRATION_STATUSES))
            .order_by(col(OrchestrationRun.started_at).asc())
        ).all()
        if run.id not in held
        and not _ticket_covered_by_ancestor_slot(session, run.ticket_id, held)
        # 645: the ancestor check reads orchestration claims only, so a
        # ticket whose own stage run holds a lane through `current_run_id`
        # was invisible here and got adopted into a second one.
        and run.ticket_id not in lane_holders
    ]
    if not orphans:
        return []

    free_slots = list(
        session.exec(
            select(AgentSlot)
            .where(AgentSlot.is_available == True)  # noqa: E712
            .order_by(AgentSlot.slot_number)
        ).all()
    )
    claimed: list[int] = []
    for run, slot in zip(orphans, free_slots, strict=False):
        slot.is_available = False
        slot.current_orchestration_run_id = run.id
        slot.current_run_id = None
        slot.assigned_at = datetime.now(timezone.utc)
        session.add(slot)
        ensure_entry(slot.slot_number, run.id)
        claimed.append(slot.slot_number)
        logger.info(
            "Claimed slot %d for orphaned orchestration %s (ticket %s)",
            slot.slot_number,
            run.id,
            run.ticket_id,
        )
    if claimed:
        session.commit()
    return claimed
