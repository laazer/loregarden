"""The lane count is a setting, and changing it strands nothing.

Lowering it is the case with consequences: idle lanes past the count go at
once, a busy one finishes its work first, and whatever was waiting in a
retired lane moves to a lane that still exists.
"""

import pytest
from loregarden.models.domain import (
    AgentSlot,
    OrchestrationRun,
    QueuedRun,
    QueuePosition,
    Ticket,
    Workspace,
)
from loregarden.services import queue_lanes
from loregarden.services.lane_count import DEFAULT_LANE_COUNT, lane_count, store_lane_count
from loregarden.services.lane_resize import resize_lanes
from loregarden.services.parallel_queue import ParallelQueueService
from loregarden.services.queue_lanes import QueueLaneService, least_busy_lane
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


class _Dispatcher:
    def __init__(self, session: Session):
        self.session = session
        self.launched: list[str] = []

    def dispatch_orchestration(self, ticket, **_options):
        self.launched.append(ticket.external_id)
        run = OrchestrationRun(
            run_code=f"orch_{ticket.external_id}",
            ticket_id=ticket.id,
            workspace_id=ticket.workspace_id,
        )
        self.session.add(run)
        self.session.commit()
        return run

    def dispatch_stage(self, ticket, entry):
        raise AssertionError("these lanes only run orchestrations")


@pytest.fixture(name="dispatcher")
def dispatcher_fixture(session):
    # `resize_lanes` builds its own lane service, so the dispatcher goes in
    # through the process-wide factory. Restored, not cleared: importing the
    # app installs the real one, and later tests on this worker depend on it.
    dispatcher = _Dispatcher(session)
    installed = queue_lanes._dispatcher_factory
    queue_lanes.set_lane_dispatcher_factory(lambda _session: dispatcher)
    yield dispatcher
    queue_lanes.set_lane_dispatcher_factory(installed)


def _slot_numbers(session: Session) -> list[int]:
    return sorted(session.exec(select(AgentSlot.slot_number)).all())


def _ticket(session: Session, workspace: Workspace, code: str) -> Ticket:
    ticket = Ticket(external_id=code, workspace_id=workspace.id, title=code)
    session.add(ticket)
    session.commit()
    return ticket


def _occupy(session: Session, workspace: Workspace, slot_number: int, code: str) -> None:
    """Put running work in a lane, so it is not idle."""
    ticket = _ticket(session, workspace, code)
    run = OrchestrationRun(run_code=f"orch_{code}", ticket_id=ticket.id, workspace_id=workspace.id)
    session.add(run)
    session.commit()
    slot = session.exec(select(AgentSlot).where(AgentSlot.slot_number == slot_number)).one()
    slot.is_available = False
    slot.current_orchestration_run_id = run.id
    session.add(slot)
    session.commit()


def _wait(session: Session, workspace: Workspace, slot_number: int, code: str) -> QueuedRun:
    entry = QueuedRun(
        workspace_id=workspace.id,
        ticket_id=_ticket(session, workspace, code).id,
        slot_number=slot_number,
        position=len(QueueLaneService(session).waiting_in_lane(slot_number)) + 1,
        status=QueuePosition.QUEUED,
    )
    session.add(entry)
    session.commit()
    return entry


def test_an_unset_lane_count_is_the_default(session):
    assert lane_count(session) == DEFAULT_LANE_COUNT
    assert ParallelQueueService(session).max_concurrent == DEFAULT_LANE_COUNT


@pytest.mark.parametrize("count", [0, 13])
def test_a_lane_count_out_of_range_is_refused(session, count):
    with pytest.raises(ValueError):
        store_lane_count(session, count)
    assert lane_count(session) == DEFAULT_LANE_COUNT


def test_raising_the_count_adds_lanes(session):
    ParallelQueueService(session).initialize_slots()

    result = resize_lanes(session, 5)

    assert _slot_numbers(session) == [1, 2, 3, 4, 5]
    assert result.retiring_lanes == []
    assert QueueLaneService(session).max_concurrent == 5


def test_lowering_the_count_retires_idle_lanes_at_once(session):
    resize_lanes(session, 5)

    result = resize_lanes(session, 2)

    assert _slot_numbers(session) == [1, 2]
    assert result.retiring_lanes == []


def test_a_busy_lane_finishes_before_it_retires(session, workspace):
    resize_lanes(session, 4)
    _occupy(session, workspace, 4, "t-running")

    result = resize_lanes(session, 2)

    assert result.retiring_lanes == [4]
    assert _slot_numbers(session) == [1, 2, 4]

    slot = session.exec(select(AgentSlot).where(AgentSlot.slot_number == 4)).one()
    slot.is_available = True
    slot.current_orchestration_run_id = None
    session.add(slot)
    session.commit()
    ParallelQueueService(session).initialize_slots()

    assert _slot_numbers(session) == [1, 2]


def test_waiting_entries_leave_a_retired_lane_in_order(session, workspace, dispatcher):
    resize_lanes(session, 3)
    _occupy(session, workspace, 1, "t-busy-1")
    _occupy(session, workspace, 2, "t-busy-2")
    _occupy(session, workspace, 3, "t-busy-3")
    _wait(session, workspace, 1, "t-lane-one")
    first = _wait(session, workspace, 3, "t-first")
    second = _wait(session, workspace, 3, "t-second")

    result = resize_lanes(session, 2)

    assert result.moved_entries == 2
    assert result.retiring_lanes == [3]
    session.refresh(first)
    session.refresh(second)
    # The emptier lane takes the first; then both lanes hold one, so the
    # lower-numbered one takes the second.
    assert (first.slot_number, first.position) == (2, 1)
    assert (second.slot_number, second.position) == (1, 2)
    assert dispatcher.launched == []


def test_an_entry_moved_into_an_idle_lane_starts(session, workspace, dispatcher):
    resize_lanes(session, 2)
    _occupy(session, workspace, 2, "t-busy")
    moved = _wait(session, workspace, 2, "t-waiting")
    # Retire nothing yet: put it past the count by hand, as an old lane would.
    moved.slot_number = 5
    session.add(moved)
    session.commit()

    resize_lanes(session, 2)

    assert dispatcher.launched == ["t-waiting"]


def test_a_retiring_lane_takes_no_new_work(session, workspace):
    resize_lanes(session, 3)
    _occupy(session, workspace, 3, "t-running")
    resize_lanes(session, 2)
    ticket = _ticket(session, workspace, "t-new")

    with pytest.raises(ValueError, match="being retired"):
        QueueLaneService(session).add_to_lane(ticket_id=ticket.id, slot_number=3)


def test_a_retried_entry_whose_lane_retired_goes_to_a_live_lane(session, workspace):
    resize_lanes(session, 3)
    _occupy(session, workspace, 1, "t-busy-1")
    _occupy(session, workspace, 2, "t-busy-2")
    retried = _wait(session, workspace, 3, "t-retried")
    resize_lanes(session, 2)
    retried.slot_number = 3  # as it was when it failed, before the count dropped
    session.add(retried)
    session.commit()

    position = QueueLaneService(session).place_at_lane_tail(retried)

    session.refresh(retried)
    assert retried.slot_number in (1, 2)
    assert position == 1


def test_new_work_goes_to_an_idle_lane_not_behind_a_running_one(session, workspace):
    """Lane 1 has a stale entry waiting, lane 2 is mid-run with nothing behind
    it, lane 3 is empty: the autopilot once picked lane 2 and the ticket waited
    out the whole run while lane 3 sat free."""
    resize_lanes(session, 3)
    _wait(session, workspace, 1, "stale")
    _occupy(session, workspace, 2, "running")

    assert least_busy_lane(QueueLaneService(session)) == 3


def test_new_work_never_goes_to_a_retired_lane(session, workspace):
    resize_lanes(session, 5)
    resize_lanes(session, 2)
    _occupy(session, workspace, 1, "one")
    _occupy(session, workspace, 2, "two")

    assert least_busy_lane(QueueLaneService(session)) in (1, 2)


def test_lane_count_endpoints(client):
    assert client.get("/api/parallel/lanes/count").json() == {
        "lane_count": DEFAULT_LANE_COUNT,
        "min": 1,
        "max": 12,
    }

    response = client.put("/api/parallel/lanes/count", json={"lane_count": 5})
    assert response.status_code == 200
    assert response.json()["lane_count"] == 5
    assert client.get("/api/parallel/lanes/count").json()["lane_count"] == 5
    assert client.get("/api/parallel/status").json()["stats"]["max_concurrent"] == 5

    assert client.put("/api/parallel/lanes/count", json={"lane_count": 0}).status_code == 422
