"""Each slot is its own serial pipeline.

A lane runs one ticket at a time and holds whatever is queued behind it. The
behaviours that matter: adding to an idle lane starts it, adding to a busy one
waits, and a lane drains itself when its orchestration finishes rather than
waiting to be poked.
"""

from unittest.mock import patch

import pytest
from loregarden.models.domain import (
    AgentSlot,
    OrchestrationRun,
    OrchestrationRunStatus,
    QueuePosition,
    Ticket,
    Workspace,
)
from loregarden.services.queue_lanes import QueueLaneService
from sqlmodel import Session, select


@pytest.fixture(name="session")
def session_fixture(isolated_db):
    with Session(isolated_db) as session:
        yield session


@pytest.fixture(name="workspace")
def workspace_fixture(session):
    ws = Workspace(slug="proj", name="proj", repo_path=".")
    session.add(ws)
    session.commit()
    session.refresh(ws)
    return ws


def _ticket(session: Session, workspace_id: str, code: str) -> Ticket:
    ticket = Ticket(external_id=code, workspace_id=workspace_id, title=code)
    session.add(ticket)
    session.commit()
    session.refresh(ticket)
    return ticket


def _orch(session: Session, ticket: Ticket, code: str) -> OrchestrationRun:
    run = OrchestrationRun(
        run_code=code,
        ticket_id=ticket.id,
        workspace_id=ticket.workspace_id,
    )
    session.add(run)
    session.commit()
    session.refresh(run)
    return run


class _Dispatcher:
    """Stands in for the orchestrator, recording what each lane launched."""

    def __init__(self, session: Session):
        self.session = session
        self.launched: list[str] = []

    def __call__(self, ticket, *, auto_approve, stop_at_stage_key):
        self.launched.append(ticket.id)
        return _orch(self.session, ticket, f"orch_{len(self.launched)}")


@pytest.fixture(name="lanes")
def lanes_fixture(session):
    service = QueueLaneService(session, max_concurrent=3)
    dispatcher = _Dispatcher(session)
    with patch.object(service, "_dispatch_orchestration", dispatcher):
        service.dispatcher = dispatcher
        yield service


def test_adding_to_an_idle_lane_starts_it(lanes, session, workspace):
    ticket = _ticket(session, workspace.id, "LG-1")

    result = lanes.add_to_lane(ticket_id=ticket.id, slot_number=2)

    assert result["status"] == "started"
    assert lanes.dispatcher.launched == [ticket.id]
    slot = session.exec(select(AgentSlot).where(AgentSlot.slot_number == 2)).one()
    assert slot.is_available is False
    assert slot.current_orchestration_run_id is not None


def test_adding_behind_a_running_ticket_waits(lanes, session, workspace):
    first = _ticket(session, workspace.id, "LG-1")
    second = _ticket(session, workspace.id, "LG-2")

    lanes.add_to_lane(ticket_id=first.id, slot_number=1)
    result = lanes.add_to_lane(ticket_id=second.id, slot_number=1)

    assert result["status"] == "queued"
    assert result["position"] == 1
    # Only the first one launched — that is the point of a serial lane.
    assert lanes.dispatcher.launched == [first.id]


def test_lanes_do_not_share_a_waiting_line(lanes, session, workspace):
    """Queueing behind lane 1 must not put anything behind lane 2."""
    a, b, c = (_ticket(session, workspace.id, f"LG-{i}") for i in range(3))

    lanes.add_to_lane(ticket_id=a.id, slot_number=1)
    lanes.add_to_lane(ticket_id=b.id, slot_number=1)
    lanes.add_to_lane(ticket_id=c.id, slot_number=2)

    assert [e.ticket_id for e in lanes.waiting_in_lane(1)] == [b.id]
    assert lanes.waiting_in_lane(2) == []
    # Lane 2 was idle, so its ticket started rather than queueing.
    assert set(lanes.dispatcher.launched) == {a.id, c.id}


def test_a_finished_orchestration_starts_the_next_in_that_lane(lanes, session, workspace):
    first = _ticket(session, workspace.id, "LG-1")
    second = _ticket(session, workspace.id, "LG-2")

    lanes.add_to_lane(ticket_id=first.id, slot_number=1)
    lanes.add_to_lane(ticket_id=second.id, slot_number=1)

    slot = session.exec(select(AgentSlot).where(AgentSlot.slot_number == 1)).one()
    lanes.on_orchestration_complete(slot.current_orchestration_run_id)

    assert lanes.dispatcher.launched == [first.id, second.id]
    assert lanes.waiting_in_lane(1) == []


def test_a_lane_that_empties_is_released(lanes, session, workspace):
    ticket = _ticket(session, workspace.id, "LG-1")
    lanes.add_to_lane(ticket_id=ticket.id, slot_number=3)

    slot = session.exec(select(AgentSlot).where(AgentSlot.slot_number == 3)).one()
    lanes.on_orchestration_complete(slot.current_orchestration_run_id)

    session.refresh(slot)
    assert slot.is_available is True
    assert slot.current_orchestration_run_id is None


def test_run_options_survive_the_wait(lanes, session, workspace):
    """The dialog is long gone by the time a queued entry starts."""
    first = _ticket(session, workspace.id, "LG-1")
    second = _ticket(session, workspace.id, "LG-2")

    lanes.add_to_lane(ticket_id=first.id, slot_number=1)
    lanes.add_to_lane(
        ticket_id=second.id,
        slot_number=1,
        auto_approve=True,
        stop_at_stage_key="verify",
    )

    entry = lanes.waiting_in_lane(1)[0]
    assert entry.auto_approve is True
    assert entry.stop_at_stage_key == "verify"


def test_removing_a_waiting_entry_closes_the_gap(lanes, session, workspace):
    running, second, third = (_ticket(session, workspace.id, f"LG-{i}") for i in range(3))

    lanes.add_to_lane(ticket_id=running.id, slot_number=1)
    lanes.add_to_lane(ticket_id=second.id, slot_number=1)
    lanes.add_to_lane(ticket_id=third.id, slot_number=1)

    assert lanes.remove_entry(lanes.waiting_in_lane(1)[0].id) is True

    remaining = lanes.waiting_in_lane(1)
    assert [e.ticket_id for e in remaining] == [third.id]
    # Positions read 1..N with no holes, or the next insert collides.
    assert [e.position for e in remaining] == [1]


def test_a_running_entry_cannot_be_removed_from_its_lane(lanes, session, workspace):
    ticket = _ticket(session, workspace.id, "LG-1")
    lanes.add_to_lane(ticket_id=ticket.id, slot_number=1)

    from loregarden.models.domain import QueuedRun

    active = session.exec(select(QueuedRun).where(QueuedRun.status == QueuePosition.ACTIVE)).one()

    assert lanes.remove_entry(active.id) is False


def test_moving_an_entry_to_an_idle_lane_starts_it(lanes, session, workspace):
    running, waiting = (_ticket(session, workspace.id, f"LG-{i}") for i in range(2))

    lanes.add_to_lane(ticket_id=running.id, slot_number=1)
    lanes.add_to_lane(ticket_id=waiting.id, slot_number=1)

    entry = lanes.waiting_in_lane(1)[0]
    assert lanes.move_entry(entry.id, slot_number=2, position=1) is True

    # Lane 2 was idle, so the move is also a start.
    assert lanes.dispatcher.launched == [running.id, waiting.id]
    assert lanes.waiting_in_lane(1) == []


def test_reordering_within_a_lane(lanes, session, workspace):
    running, a, b = (_ticket(session, workspace.id, f"LG-{i}") for i in range(3))

    lanes.add_to_lane(ticket_id=running.id, slot_number=1)
    lanes.add_to_lane(ticket_id=a.id, slot_number=1)
    lanes.add_to_lane(ticket_id=b.id, slot_number=1)

    last = lanes.waiting_in_lane(1)[-1]
    lanes.move_entry(last.id, slot_number=1, position=1)

    assert [e.ticket_id for e in lanes.waiting_in_lane(1)] == [b.id, a.id]
    assert [e.position for e in lanes.waiting_in_lane(1)] == [1, 2]


def test_an_orchestration_for_no_lane_is_ignored(lanes, session, workspace):
    """Runs started from the Dashboard hold no lane and must not free one."""
    ticket = _ticket(session, workspace.id, "LG-1")
    lanes.add_to_lane(ticket_id=ticket.id, slot_number=1)
    stray = _orch(session, ticket, "orch_stray")

    lanes.on_orchestration_complete(stray.id)

    slot = session.exec(select(AgentSlot).where(AgentSlot.slot_number == 1)).one()
    assert slot.is_available is False


def test_a_vanished_ticket_does_not_wedge_the_lane(lanes, session, workspace):
    running, doomed, good = (_ticket(session, workspace.id, f"LG-{i}") for i in range(3))

    lanes.add_to_lane(ticket_id=running.id, slot_number=1)
    lanes.add_to_lane(ticket_id=doomed.id, slot_number=1)
    lanes.add_to_lane(ticket_id=good.id, slot_number=1)

    session.delete(session.get(Ticket, doomed.id))
    session.commit()

    slot = session.exec(select(AgentSlot).where(AgentSlot.slot_number == 1)).one()
    lanes.on_orchestration_complete(slot.current_orchestration_run_id)

    # It skipped the dead entry rather than stalling on it.
    assert lanes.dispatcher.launched == [running.id, good.id]


def test_completion_releases_the_lane_through_the_orchestrator(session, workspace):
    """The wiring, not the service: `complete_orchestration` must free the lane.

    Hooked there because that is where every orchestration reaches a terminal
    status, whatever drove it.
    """
    from loregarden.services.orchestration_callbacks import OrchestrationCallbackService

    service = QueueLaneService(session, max_concurrent=3)
    dispatcher = _Dispatcher(session)
    ticket = _ticket(session, workspace.id, "LG-1")

    with patch.object(service, "_dispatch_orchestration", dispatcher):
        service.add_to_lane(ticket_id=ticket.id, slot_number=1)

    slot = session.exec(select(AgentSlot).where(AgentSlot.slot_number == 1)).one()
    orch_run = session.get(OrchestrationRun, slot.current_orchestration_run_id)

    OrchestrationCallbackService(session).complete_orchestration(
        orch_run, ticket, status=OrchestrationRunStatus.SUCCEEDED
    )

    session.refresh(slot)
    assert slot.is_available is True
