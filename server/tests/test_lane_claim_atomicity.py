"""The lane's slot claim, hardened the way admission's already was.

e2 replaced the slot claim with a conditional UPDATE so the database picks the
winner in one statement. That fixed `claim_free_slot`, which the *admission*
path uses. There are two claim sites and only one was hardened:
`QueueLaneService.start_lane_head` still read `slot.is_available`, ran the whole
dispatch, and only then wrote `is_available = False`. A concurrent admission —
a different request, its own session — could claim that slot atomically inside
the window, and the lane then overwrote the row. One slot, two live occupants,
and the pool had admitted past `max_concurrent`.

The ordering was deliberate, which is why it survived: the lane claimed *after*
dispatch so a refused dispatch would not strand a lane. Both properties are
required, so the claim moves ahead of the dispatch and is given back when the
dispatcher refuses — the shape `Reservation` already uses on the admission side.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from loregarden.models.domain import (
    AgentSlot,
    OrchestrationRun,
    QueuePosition,
    Ticket,
    WorkItemType,
)
from loregarden.services.parallel_queue import claim_free_slot
from loregarden.services.queue_lanes import QueueLaneService
from loregarden.services.ticket_service import TicketService
from sqlmodel import select


@pytest.fixture(name="lanes")
def lanes_fixture(db_session) -> QueueLaneService:
    """A three-lane pool with every lane occupied except lane 1."""
    service = QueueLaneService(db_session, max_concurrent=3)
    service.slots.initialize_slots()
    for slot in db_session.exec(select(AgentSlot)).all():
        slot.is_available = False
        db_session.add(slot)
    db_session.commit()
    return service


def _ticket(db_session, title: str) -> Ticket:
    return TicketService(db_session).create_ticket(
        workspace_slug="loregarden",
        title=title,
        work_item_type=WorkItemType.MILESTONE,
    )


def _free_lane_one(db_session) -> AgentSlot:
    slot = db_session.exec(select(AgentSlot).where(AgentSlot.slot_number == 1)).one()
    slot.is_available = True
    db_session.add(slot)
    db_session.commit()
    return slot


def _orch_run(db_session, ticket: Ticket) -> OrchestrationRun:
    run = OrchestrationRun(
        ticket_id=ticket.id,
        workspace_id=ticket.workspace_id,
        run_code="orch_lane_test",
        profile_slug="loregarden",
    )
    db_session.add(run)
    db_session.commit()
    db_session.refresh(run)
    return run


class _Dispatcher:
    """A dispatcher that lets a concurrent admission in mid-dispatch."""

    def __init__(self, db_session, orch_run: OrchestrationRun | None):
        self.db_session = db_session
        self.orch_run = orch_run
        self.rival_slot: AgentSlot | None = None

    def dispatch_orchestration(self, ticket, **kwargs):
        self.rival_slot = claim_free_slot(self.db_session)
        return self.orch_run

    def dispatch_stage(self, ticket, entry):
        return None


class _RefusingDispatcher:
    """Refuses, and claims nothing — so the lane's own bookkeeping is what shows."""

    def dispatch_orchestration(self, ticket, **kwargs):
        return None

    def dispatch_stage(self, ticket, entry):
        return None


# ---- the race ----------------------------------------------------------


def test_admission_cannot_take_the_lane_mid_dispatch(db_session, lanes):
    """The window between the availability check and the write.

    The rival claim stands in for a concurrent admission request. With the lane
    claimed up front there is nothing for it to take, so it correctly finds a
    full pool.
    """
    ticket = _ticket(db_session, "lane claim — the race")
    lanes.add_to_lane(ticket_id=ticket.id, slot_number=1, entry_kind="orchestration")
    _free_lane_one(db_session)

    dispatcher = _Dispatcher(db_session, _orch_run(db_session, ticket))
    with patch.object(lanes, "dispatcher", dispatcher):
        lanes.start_lane_head(1)

    assert dispatcher.rival_slot is None, (
        "a concurrent admission claimed the lane the dispatch was already using"
    )


def test_the_lane_records_the_run_it_started(db_session, lanes):
    """The claim moving earlier must not cost the binding it used to write."""
    ticket = _ticket(db_session, "lane claim — binding")
    lanes.add_to_lane(ticket_id=ticket.id, slot_number=1, entry_kind="orchestration")
    _free_lane_one(db_session)
    orch_run = _orch_run(db_session, ticket)

    with patch.object(lanes, "dispatcher", _Dispatcher(db_session, orch_run)):
        head = lanes.start_lane_head(1)

    db_session.expire_all()
    slot = db_session.exec(select(AgentSlot).where(AgentSlot.slot_number == 1)).one()
    assert slot.is_available is False
    assert slot.current_orchestration_run_id == orch_run.id
    assert head is not None
    assert head.status == QueuePosition.ACTIVE


# ---- a refusal must not strand the lane --------------------------------


def test_a_refused_dispatch_gives_the_lane_back(db_session, lanes):
    """Claiming first is only safe if a refusal releases.

    This is the property the original ordering protected, and the reason the
    check-then-act survived: a lane claimed for a dispatch that never happened
    is a lane nothing will ever free.
    """
    ticket = _ticket(db_session, "lane claim — refused")
    lanes.add_to_lane(ticket_id=ticket.id, slot_number=1, entry_kind="orchestration")
    _free_lane_one(db_session)

    with patch.object(lanes, "dispatcher", _RefusingDispatcher()):
        assert lanes.start_lane_head(1) is None

    db_session.expire_all()
    slot = db_session.exec(select(AgentSlot).where(AgentSlot.slot_number == 1)).one()
    assert slot.is_available is True, "a refused dispatch stranded the lane"
    assert lanes.waiting_in_lane(1), "the entry must stay queued for the next attempt"


def test_an_empty_lane_does_not_hold_a_slot(db_session, lanes):
    """Nothing waiting is not a reason to take a lane."""
    _free_lane_one(db_session)

    assert lanes.start_lane_head(1) is None

    db_session.expire_all()
    slot = db_session.exec(select(AgentSlot).where(AgentSlot.slot_number == 1)).one()
    assert slot.is_available is True


def test_an_occupied_lane_is_left_alone(db_session, lanes):
    """Lane 1 stays busy: nothing is dispatched and nothing is claimed."""
    ticket = _ticket(db_session, "lane claim — occupied")
    lanes.add_to_lane(ticket_id=ticket.id, slot_number=1, entry_kind="orchestration")

    with patch.object(lanes, "dispatcher", _RefusingDispatcher()):
        assert lanes.start_lane_head(1) is None
