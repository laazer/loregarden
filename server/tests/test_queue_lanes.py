"""Each slot is its own serial pipeline.

A lane runs one ticket at a time and holds whatever is queued behind it. The
behaviours that matter: adding to an idle lane starts it, adding to a busy one
waits, and a lane drains itself when its orchestration finishes rather than
waiting to be poked.
"""

import subprocess
import sys

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
from loregarden.services.ticket_service import TicketService
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
    """Stands in for the orchestrator, recording what each lane launched.

    Injected rather than patched: dispatch lives above the lane now (see
    `queue_dispatch`), and `LaneDispatcher` is the seam it calls through.
    """

    def __init__(self, session: Session):
        self.session = session
        self.launched: list[str] = []
        #: Stand in for a refused dispatch — already orchestrating, no workflow,
        #: unknown driver. The lane is left unclaimed and the entry queued.
        self.refuse = False

    def dispatch_orchestration(
        self,
        ticket,
        *,
        auto_approve,
        stop_at_stage_key,
        driver="",
        max_stages=None,
        timeout_seconds=None,
    ):
        if self.refuse:
            return None
        self.launched.append(ticket.id)
        return _orch(self.session, ticket, f"orch_{len(self.launched)}")

    def dispatch_stage(self, ticket, entry):
        raise AssertionError("these lanes only run orchestrations")


@pytest.fixture(name="lanes")
def lanes_fixture(session):
    dispatcher = _Dispatcher(session)
    service = QueueLaneService(session, max_concurrent=3, dispatcher=dispatcher)
    service.dispatcher = dispatcher
    yield service


@pytest.mark.parametrize(
    "entry_point",
    ["loregarden.main", "loregarden.cli.mcp_server", "loregarden.cli.main", "loregarden.mcp.tools"],
)
def test_every_entry_point_installs_a_lane_dispatcher(entry_point):
    """The wiring dispatch-above-the-lane depends on.

    `queue_lanes` resolves its dispatcher at runtime so it does not have to
    import the orchestrator, which is what broke the cycle. The cost is that a
    process which never imports `queue_dispatch` has lanes that start nothing —
    so every process that can start work is pinned here rather than trusted.
    """
    # A fresh interpreter, because `import_module` on an already-imported entry
    # point is a no-op — the assertion would pass on wiring some earlier test
    # did, which is exactly the regression this is meant to catch.
    probe = (
        f"import {entry_point};"
        "from loregarden.services import queue_lanes;"
        "import sys; sys.exit(0 if queue_lanes._dispatcher_factory else 1)"
    )
    result = subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True)

    assert result.returncode == 0, (
        f"{entry_point} can start work but installs no lane dispatcher\n{result.stderr}"
    )


def test_a_lane_with_no_dispatcher_refuses_rather_than_silently_idling(session, workspace, caplog):
    """The failure mode the registry introduces must be loud.

    A lane that quietly starts nothing is the bug this whole audit was about;
    an unwired process says so in the log instead.
    """
    service = QueueLaneService(session, max_concurrent=3, dispatcher=None)
    service.dispatcher = None
    ticket = _ticket(session, workspace.id, "LG-1")
    service.add_to_lane(ticket_id=ticket.id, slot_number=1)

    with caplog.at_level("ERROR"):
        assert service.start_lane_head(1) is None

    assert "no dispatcher is installed" in caplog.text
    # The entry keeps its place rather than being consumed by a failed start.
    assert [e.ticket_id for e in service.waiting_in_lane(1)] == [ticket.id]


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

    # Deleted the way the app deletes a ticket. A raw `session.delete` leaves
    # the lane entry pointing at nothing, which foreign-key enforcement now
    # rejects outright — the state this used to simulate is unreachable.
    TicketService(session).delete_ticket(doomed.id)

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

    dispatcher = _Dispatcher(session)
    service = QueueLaneService(session, max_concurrent=3, dispatcher=dispatcher)
    ticket = _ticket(session, workspace.id, "LG-1")

    service.add_to_lane(ticket_id=ticket.id, slot_number=1)

    slot = session.exec(select(AgentSlot).where(AgentSlot.slot_number == 1)).one()
    orch_run = session.get(OrchestrationRun, slot.current_orchestration_run_id)

    OrchestrationCallbackService(session).complete_orchestration(
        orch_run, ticket, status=OrchestrationRunStatus.SUCCEEDED
    )

    session.refresh(slot)
    assert slot.is_available is True


def test_blocking_a_ticket_releases_the_lane(session, workspace):
    """`block_ticket` is a terminal status too, and used to skip the release.

    It set BLOCKED and finished_at directly instead of going through the exit
    every other terminal path uses, so the lane entry stayed ACTIVE and
    `ticket_activity` reported the ticket as running for as long as the row
    existed.
    """
    from loregarden.models.domain import QueuedRun
    from loregarden.services.orchestration_callbacks import OrchestrationCallbackService

    dispatcher = _Dispatcher(session)
    service = QueueLaneService(session, max_concurrent=3, dispatcher=dispatcher)
    blocked = _ticket(session, workspace.id, "LG-1")

    service.add_to_lane(ticket_id=blocked.id, slot_number=1)

    slot = session.exec(select(AgentSlot).where(AgentSlot.slot_number == 1)).one()
    orch_run = session.get(OrchestrationRun, slot.current_orchestration_run_id)

    OrchestrationCallbackService(session).block_ticket(
        orch_run, blocked, message="Tests failed after 3 attempts"
    )

    entry = session.exec(
        select(QueuedRun).where(QueuedRun.orchestration_run_id == orch_run.id)
    ).one()
    assert entry.status == QueuePosition.STARTED
    session.refresh(slot)
    assert slot.is_available is True


def test_reconcile_settles_an_entry_whose_orchestration_already_finished(lanes, session, workspace):
    """The sweep for residue neither release path got to.

    A restart between "the run went terminal" and "the entry was retired" leaves
    an ACTIVE entry that no later pass can find by itself: `reconcile_slots`
    reclaims the slot independently, and once it stops naming the orchestration
    nothing ties the two together. `ticket_activity` reads ACTIVE as running, so
    the ticket claims an agent forever.
    """
    from loregarden.models.domain import QueuedRun

    ticket = _ticket(session, workspace.id, "LG-1")
    lanes.add_to_lane(ticket_id=ticket.id, slot_number=1)

    slot = session.exec(select(AgentSlot).where(AgentSlot.slot_number == 1)).one()
    orch = session.get(OrchestrationRun, slot.current_orchestration_run_id)
    orch.status = OrchestrationRunStatus.BLOCKED
    session.add(orch)
    # The slot was reclaimed already; only the entry is left behind.
    slot.is_available = True
    slot.current_orchestration_run_id = None
    session.add(slot)
    session.commit()

    lanes.reconcile_lanes()

    entry = session.exec(select(QueuedRun).where(QueuedRun.orchestration_run_id == orch.id)).one()
    assert entry.status == QueuePosition.STARTED


def test_reconcile_settles_a_stage_entry_whose_run_succeeded(lanes, session, workspace):
    """The run-side half: success never wrote the entry's status at all.

    `on_run_complete_sync` frees the slot and marks the entry FAILED only when
    the run failed, so a stage entry that *succeeded* stayed ACTIVE — the same
    permanent ghost, reached through the other regime in `queued_runs`.
    """
    from loregarden.models.domain import AgentRun, QueuedRun, RunStatus

    ticket = _ticket(session, workspace.id, "LG-1")
    run = AgentRun(
        run_code="run_1",
        ticket_id=ticket.id,
        workspace_id=workspace.id,
        agent_id="implementer",
        status=RunStatus.SUCCEEDED,
    )
    session.add(run)
    entry = QueuedRun(
        workspace_id=workspace.id,
        ticket_id=ticket.id,
        run_id=run.id,
        slot_number=1,
        status=QueuePosition.ACTIVE,
        entry_kind="stage",
        stage_key="implement",
    )
    session.add(entry)
    session.commit()

    lanes.reconcile_lanes()

    session.refresh(entry)
    assert entry.status == QueuePosition.STARTED


def test_reconcile_leaves_a_live_entry_alone(lanes, session, workspace):
    """The sweep must not retire a lane that is genuinely working."""
    from loregarden.models.domain import QueuedRun

    ticket = _ticket(session, workspace.id, "LG-1")
    lanes.add_to_lane(ticket_id=ticket.id, slot_number=1)

    slot = session.exec(select(AgentSlot).where(AgentSlot.slot_number == 1)).one()
    orch_id = slot.current_orchestration_run_id

    lanes.reconcile_lanes()

    entry = session.exec(select(QueuedRun).where(QueuedRun.orchestration_run_id == orch_id)).one()
    assert entry.status == QueuePosition.ACTIVE


def test_the_entry_settles_even_when_the_slot_is_already_gone(lanes, session, workspace):
    """`reconcile_slots` reclaims a terminal occupant's slot on any status read.

    When it wins that race the slot no longer names the orchestration, and
    gating the entry update on that lookup left the entry ACTIVE with nothing
    able to find it again — a finished ticket reading "running" forever.
    """
    from loregarden.models.domain import QueuedRun

    ticket = _ticket(session, workspace.id, "LG-1")
    lanes.add_to_lane(ticket_id=ticket.id, slot_number=1)

    slot = session.exec(select(AgentSlot).where(AgentSlot.slot_number == 1)).one()
    orch_id = slot.current_orchestration_run_id

    # Whoever got there first: the slot is back in the pool, pointing at nothing.
    slot.is_available = True
    slot.current_orchestration_run_id = None
    session.add(slot)
    session.commit()

    lanes.on_orchestration_complete(orch_id)

    entry = session.exec(select(QueuedRun).where(QueuedRun.orchestration_run_id == orch_id)).one()
    assert entry.status == QueuePosition.STARTED


def test_a_lane_whose_dispatch_was_refused_is_retried(lanes, session, workspace):
    """A refusal is not an event on the lane, so nothing used to retry it.

    `start_lane_head` leaves the entry queued and the lane unclaimed when a
    dispatch is refused — the ticket is already orchestrating, its workflow is
    gone, the driver name is unknown. That is right in the moment and wedges the
    lane forever: `reconcile_lanes` only restarts lanes in `freed`, `freed` comes
    from `reconcile_slots`, and that selects slots which were *taken*. A lane
    never claimed is in no list. The other callers are an add, a move and a
    completion, and a refusal is none of them.
    """
    ticket = _ticket(session, workspace.id, "LG-1")
    lanes.dispatcher.refuse = True
    lanes.add_to_lane(ticket_id=ticket.id, slot_number=1)

    # Refused: the entry is still waiting and the lane was never claimed.
    assert [e.ticket_id for e in lanes.waiting_in_lane(1)] == [ticket.id]
    slot = session.exec(select(AgentSlot).where(AgentSlot.slot_number == 1)).one()
    assert slot.is_available is True

    lanes.dispatcher.refuse = False
    lanes.reconcile_lanes()

    assert lanes.dispatcher.launched == [ticket.id]
    assert lanes.waiting_in_lane(1) == []


def test_reconcile_does_not_disturb_a_lane_that_is_genuinely_idle(lanes, session, workspace):
    """An empty lane must not be poked into starting something."""
    lanes.reconcile_lanes()
    assert lanes.dispatcher.launched == []
