"""Turning an autopilot off means "finish what is running, then stop".

A pick waits in its lane until the slot frees, and the lane starts it then
whatever the autopilot's switch says. These pin that off holds what it queued
and has not started, leaves what is running alone, and that on puts back
exactly what off held.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from loregarden.models.domain import (
    AutopilotAction,
    AutopilotEvent,
    AutopilotUpdate,
    InitiativePlan,
    QueuedRun,
    QueuePosition,
    Ticket,
    TicketState,
    WorkItemType,
)
from loregarden.services.initiative_autopilot import BREAKER_LIMIT, run_autopilot, set_autopilot
from loregarden.services.queue_lanes import QueueLaneService
from loregarden.services.ticket_service import TicketService
from sqlmodel import Session, select
from tests.test_initiative_graph import (  # noqa: F401 - fixtures are used by name
    NOW,
    _item,
    workspaces_fixture,
)
from tests.test_queue_lanes import _Dispatcher


@pytest.fixture(name="lanes")
def lanes_fixture(db_session: Session):
    dispatcher = _Dispatcher(db_session)
    service = QueueLaneService(db_session, max_concurrent=3, dispatcher=dispatcher)
    with patch("loregarden.services.autopilot_hold.QueueLaneService", return_value=service):
        yield service


@pytest.fixture(name="root")
def root_fixture(db_session: Session, workspaces) -> Ticket:
    here, _ = workspaces
    return TicketService(db_session).create_ticket(
        workspace_slug=here.slug, title="Root", work_item_type=WorkItemType.MILESTONE
    )


def _switch(session: Session, root: Ticket, enabled: bool) -> None:
    set_autopilot(session, root.id, AutopilotUpdate(enabled=enabled), actor="t")


def _dispatched(session: Session, root: Ticket, ticket: Ticket) -> None:
    """What `_dispatch` logs when it queues a ticket."""
    session.add(
        AutopilotEvent(
            initiative_id=root.id, action=AutopilotAction.DISPATCHED, ticket_id=ticket.id
        )
    )
    session.commit()


def _queue(session, lanes, root, ticket, *, slot=1, by_autopilot=True) -> QueuedRun:
    result = lanes.add_to_lane(ticket_id=ticket.id, slot_number=slot, auto_approve=True)
    if by_autopilot:
        _dispatched(session, root, ticket)
    return session.get(QueuedRun, result["entry_id"])


def _status(session: Session, entry: QueuedRun) -> QueuePosition:
    session.refresh(entry)
    return entry.status


def _actions(session: Session, root: Ticket, action: AutopilotAction) -> list[str | None]:
    return list(
        session.exec(
            select(AutopilotEvent.ticket_id).where(
                AutopilotEvent.initiative_id == root.id, AutopilotEvent.action == action
            )
        ).all()
    )


@pytest.fixture(name="busy_lane")
def busy_lane_fixture(db_session, workspaces, lanes, root):
    """Lane 1 running `first`, with `second` and `third` queued behind it."""
    here, _ = workspaces
    first, second, third = (_item(db_session, here, root, name) for name in ("a", "b", "c"))
    _switch(db_session, root, True)
    running = _queue(db_session, lanes, root, first)
    waiting = [_queue(db_session, lanes, root, t) for t in (second, third)]
    assert lanes.dispatcher.launched == [first.id]
    assert [_status(db_session, e) for e in waiting] == [QueuePosition.QUEUED] * 2
    return running, waiting


def test_turning_off_holds_what_it_queued_and_leaves_the_running_ticket_alone(
    db_session, lanes, root, busy_lane
):
    running, waiting = busy_lane
    running_status = _status(db_session, running)

    _switch(db_session, root, False)

    assert [_status(db_session, e) for e in waiting] == [QueuePosition.PAUSED] * 2
    assert _status(db_session, running) == running_status
    assert set(_actions(db_session, root, AutopilotAction.HELD)) == {e.ticket_id for e in waiting}


def test_turning_off_again_holds_work_it_queued_before_the_last_cycle(
    db_session, lanes, root, busy_lane
):
    _, waiting = busy_lane
    _switch(db_session, root, False)
    _switch(db_session, root, True)
    assert [_status(db_session, e) for e in waiting] == [QueuePosition.QUEUED] * 2

    _switch(db_session, root, False)

    assert [_status(db_session, e) for e in waiting] == [QueuePosition.PAUSED] * 2


def test_a_held_entry_is_not_started_when_its_lane_frees(db_session, lanes, root, busy_lane):
    _, waiting = busy_lane
    _switch(db_session, root, False)

    # The lane is free and would start its head — there is none to start.
    held = waiting[0]
    held.slot_number = 2
    db_session.add(held)
    db_session.commit()
    lanes.start_lane_head(2)

    assert held.ticket_id not in lanes.dispatcher.launched
    assert lanes.waiting_in_lane(1) == []


def test_turning_off_leaves_an_entry_a_person_queued(db_session, workspaces, lanes, root):
    here, _ = workspaces
    first, theirs = _item(db_session, here, root, "a"), _item(db_session, here, root, "b")
    _switch(db_session, root, True)
    _queue(db_session, lanes, root, first)
    entry = _queue(db_session, lanes, root, theirs, by_autopilot=False)

    _switch(db_session, root, False)

    assert _status(db_session, entry) == QueuePosition.QUEUED


def test_turning_off_leaves_a_ticket_a_person_queued_again_after_it_ran(
    db_session, workspaces, lanes, root, busy_lane
):
    """Dispatched once, long finished, queued again by hand: that entry is theirs."""
    here, _ = workspaces
    again = _item(db_session, here, root, "again")
    _dispatched(db_session, root, again)
    entry = _queue(db_session, lanes, root, again, by_autopilot=False)

    _switch(db_session, root, False)

    assert _status(db_session, entry) == QueuePosition.QUEUED


def test_turning_back_on_puts_held_work_back_at_the_end_of_its_lane(
    db_session, workspaces, lanes, root, busy_lane
):
    here, _ = workspaces
    _, waiting = busy_lane
    _switch(db_session, root, False)
    # Queued by a person while the autopilot was off: it is ahead now.
    later = _queue(db_session, lanes, root, _item(db_session, here, root, "d"), by_autopilot=False)
    assert later.position == 1

    _switch(db_session, root, True)

    assert [e.id for e in lanes.waiting_in_lane(1)] == [later.id, waiting[0].id, waiting[1].id]
    assert set(_actions(db_session, root, AutopilotAction.RELEASED)) == {
        e.ticket_id for e in waiting
    }


def test_a_held_entry_released_into_a_free_lane_starts(db_session, lanes, root, busy_lane):
    _, waiting = busy_lane
    _switch(db_session, root, False)
    held = waiting[0]
    held.slot_number = 2
    db_session.add(held)
    db_session.commit()

    _switch(db_session, root, True)

    assert lanes.dispatcher.launched[-1] == held.ticket_id


def test_a_ticket_that_finished_while_held_is_not_run_again(db_session, lanes, root, busy_lane):
    _, waiting = busy_lane
    _switch(db_session, root, False)
    done = db_session.get(Ticket, waiting[0].ticket_id)
    done.state = TicketState.DONE
    db_session.add(done)
    db_session.commit()

    _switch(db_session, root, True)

    assert _status(db_session, waiting[0]) == QueuePosition.CANCELLED
    assert _status(db_session, waiting[1]) == QueuePosition.QUEUED


def test_a_ticket_queued_again_while_held_is_not_queued_twice(db_session, lanes, root, busy_lane):
    _, waiting = busy_lane
    _switch(db_session, root, False)
    again = _queue(
        db_session, lanes, root, db_session.get(Ticket, waiting[0].ticket_id), by_autopilot=False
    )

    _switch(db_session, root, True)

    assert _status(db_session, waiting[0]) == QueuePosition.CANCELLED
    assert [e.ticket_id for e in lanes.waiting_in_lane(1)].count(waiting[0].ticket_id) == 1
    assert _status(db_session, again) == QueuePosition.QUEUED


def test_turning_on_leaves_an_entry_a_person_paused(db_session, lanes, root, busy_lane):
    _, waiting = busy_lane
    _switch(db_session, root, False)
    _switch(db_session, root, True)
    # Paused by hand after the release: no hold of the autopilot's covers it.
    waiting[0].status = QueuePosition.PAUSED
    db_session.add(waiting[0])
    db_session.commit()

    _switch(db_session, root, False)
    _switch(db_session, root, True)

    assert _status(db_session, waiting[0]) == QueuePosition.PAUSED
    assert _status(db_session, waiting[1]) == QueuePosition.QUEUED


def test_stopping_itself_holds_what_it_queued(db_session, workspaces, lanes, root, busy_lane):
    here, _ = workspaces
    _, waiting = busy_lane
    for n in range(BREAKER_LIMIT):
        blocked = _item(db_session, here, root, f"blocked-{n}")
        blocked.state = TicketState.BLOCKED
        db_session.add(blocked)
        _dispatched(db_session, root, blocked)

    run_autopilot(db_session, root.id, now=NOW)

    assert db_session.get(InitiativePlan, root.id).autopilot is False
    assert [_status(db_session, e) for e in waiting] == [QueuePosition.PAUSED] * 2
