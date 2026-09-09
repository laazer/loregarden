"""A block that may still resolve keeps its lane.

Before repair holds, a ticket that blocked lost its lane at once: the slot was
released, the next entry started, and any repair had to re-enter admission and
queue behind it. These cover the three things that makes safe — the hold itself,
the driver that spends it, and the two caps that end it — and the one thing that
must not change: a block with no repair route still settles immediately.
"""

from datetime import datetime, timedelta, timezone

import pytest
from loregarden.models.domain import (
    AgentSlot,
    Approval,
    ApprovalStatus,
    OrchestrationRun,
    OrchestrationRunStatus,
    QueuedRun,
    QueuePosition,
    RepairRoute,
    StageStatus,
    Ticket,
    TicketState,
    Workspace,
)
from loregarden.services.parallel_queue import ParallelQueueService
from loregarden.services.queue_lanes import QueueLaneService
from loregarden.services.queue_repair import (
    REPAIR_ATTEMPT_CAP,
    REPAIR_WALL_CLOCK,
    resolve_repair_route,
)
from loregarden.services.run_interruption import INTERRUPTED_RUN_MESSAGE
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
    run = OrchestrationRun(run_code=code, ticket_id=ticket.id, workspace_id=ticket.workspace_id)
    session.add(run)
    session.commit()
    session.refresh(run)
    return run


class _Dispatcher:
    def __init__(self, session: Session):
        self.session = session
        self.launched: list[str] = []
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
    return service


def _block(session: Session, ticket: Ticket, *, message: str = INTERRUPTED_RUN_MESSAGE) -> None:
    """Leave the ticket in the state a blocked run leaves behind."""
    ticket.state = TicketState.BLOCKED
    ticket.workflow_stage_key = "implement"
    ticket.workflow_stage_status = StageStatus.BLOCKED
    ticket.blocking_issues = message
    session.add(ticket)
    session.commit()


def _finish_blocked(lanes: QueueLaneService, session: Session, ticket: Ticket) -> QueuedRun:
    """Run a ticket in lane 1 and end its orchestration BLOCKED."""
    lanes.add_to_lane(ticket_id=ticket.id, slot_number=1)
    entry = session.exec(select(QueuedRun).where(QueuedRun.ticket_id == ticket.id)).one()
    orch = session.get(OrchestrationRun, entry.orchestration_run_id)
    orch.status = OrchestrationRunStatus.BLOCKED
    session.add(orch)
    session.commit()
    lanes.on_orchestration_complete(orch.id)
    session.refresh(entry)
    return entry


def _slot(session: Session, number: int) -> AgentSlot:
    return session.exec(select(AgentSlot).where(AgentSlot.slot_number == number)).one()


def test_a_repairable_block_keeps_its_lane(lanes, session, workspace):
    held = _ticket(session, workspace.id, "LG-1")
    behind = _ticket(session, workspace.id, "LG-2")
    lanes.add_to_lane(ticket_id=held.id, slot_number=1)
    lanes.add_to_lane(ticket_id=behind.id, slot_number=1)
    _block(session, held)

    entry = session.exec(select(QueuedRun).where(QueuedRun.ticket_id == held.id)).one()
    orch = session.get(OrchestrationRun, entry.orchestration_run_id)
    orch.status = OrchestrationRunStatus.BLOCKED
    session.add(orch)
    session.commit()
    lanes.on_orchestration_complete(orch.id)

    session.refresh(entry)
    assert entry.status == QueuePosition.REPAIRING
    assert entry.repair_route is RepairRoute.INTERRUPTION
    assert _slot(session, 1).is_available is False
    # The whole point: the ticket behind it did not take the lane.
    assert lanes.dispatcher.launched == [held.id]


def test_a_final_block_still_releases_the_lane_at_once(lanes, session, workspace):
    """The unchanged path. A block with no repair route is over on arrival."""
    blocked = _ticket(session, workspace.id, "LG-1")
    behind = _ticket(session, workspace.id, "LG-2")
    lanes.add_to_lane(ticket_id=blocked.id, slot_number=1)
    lanes.add_to_lane(ticket_id=behind.id, slot_number=1)
    _block(session, blocked, message="The API key this needs does not exist.")

    entry = session.exec(select(QueuedRun).where(QueuedRun.ticket_id == blocked.id)).one()
    orch = session.get(OrchestrationRun, entry.orchestration_run_id)
    orch.status = OrchestrationRunStatus.BLOCKED
    session.add(orch)
    session.commit()
    lanes.on_orchestration_complete(orch.id)

    session.refresh(entry)
    assert entry.status == QueuePosition.STARTED
    assert lanes.dispatcher.launched == [blocked.id, behind.id]


def test_reconcile_spends_the_hold_on_a_re_dispatch(lanes, session, workspace):
    ticket = _ticket(session, workspace.id, "LG-1")
    _block(session, ticket)
    entry = _finish_blocked(lanes, session, ticket)
    assert entry.status == QueuePosition.REPAIRING

    lanes.reconcile_lanes()

    session.refresh(entry)
    assert entry.status == QueuePosition.ACTIVE
    assert entry.repair_attempts == 1
    assert entry.repairing_since is None
    # A second, real dispatch of the same ticket — that is what a repair is.
    assert lanes.dispatcher.launched == [ticket.id, ticket.id]
    assert _slot(session, 1).current_orchestration_run_id == entry.orchestration_run_id


def test_a_refused_repair_spends_an_attempt_and_keeps_holding(lanes, session, workspace):
    ticket = _ticket(session, workspace.id, "LG-1")
    _block(session, ticket)
    entry = _finish_blocked(lanes, session, ticket)

    lanes.dispatcher.refuse = True
    lanes.reconcile_lanes()

    session.refresh(entry)
    assert entry.status == QueuePosition.REPAIRING
    assert entry.repair_attempts == 1
    assert entry.last_failed_at is not None


def test_the_attempt_cap_ends_the_hold_and_drains_the_lane(lanes, session, workspace):
    ticket = _ticket(session, workspace.id, "LG-1")
    behind = _ticket(session, workspace.id, "LG-2")
    _block(session, ticket)
    entry = _finish_blocked(lanes, session, ticket)
    lanes.add_to_lane(ticket_id=behind.id, slot_number=1)
    entry.repair_attempts = REPAIR_ATTEMPT_CAP
    session.add(entry)
    session.commit()

    lanes.reconcile_lanes()

    session.refresh(entry)
    assert entry.status == QueuePosition.STARTED
    assert str(REPAIR_ATTEMPT_CAP) in entry.failure_reason
    assert behind.id in lanes.dispatcher.launched


def test_the_wall_clock_cap_ends_a_hold_nothing_can_start(lanes, session, workspace):
    ticket = _ticket(session, workspace.id, "LG-1")
    _block(session, ticket)
    entry = _finish_blocked(lanes, session, ticket)
    entry.repairing_since = datetime.now(timezone.utc) - REPAIR_WALL_CLOCK - timedelta(seconds=1)
    session.add(entry)
    session.commit()

    lanes.dispatcher.refuse = True
    lanes.reconcile_lanes()

    session.refresh(entry)
    assert entry.status == QueuePosition.STARTED
    assert _slot(session, 1).is_available is True
    # No further attempt was spent — the clock ended it before the dispatch.
    assert entry.repair_attempts == 0


def test_the_slot_sweep_leaves_a_held_lane_alone(lanes, session, workspace):
    """`reconcile_slots` reclaims slots whose occupant is terminal, and a held
    lane names a BLOCKED orchestration — so without the hold-aware predicate the
    first sweep would take the lane back and the hold would protect nothing."""
    ticket = _ticket(session, workspace.id, "LG-1")
    _block(session, ticket)
    _finish_blocked(lanes, session, ticket)

    freed = ParallelQueueService(session, max_concurrent=3).reconcile_slots()

    assert freed == []
    assert _slot(session, 1).is_available is False


def test_the_retry_breakers_own_block_is_not_a_repair_route(session, workspace):
    """It fired to stop this stage being dispatched again. Repairing it would
    hand back exactly the retry the breaker refused."""
    from loregarden.services.stage_retry_budget import record_stage_retry_block

    ticket = _ticket(session, workspace.id, "LG-1")
    _block(session, ticket, message="Stage 'implement' reached its retry budget.")
    record_stage_retry_block(session, ticket.id, "implement")

    assert resolve_repair_route(session, ticket) is None


def test_a_ticket_waiting_on_a_person_is_not_repaired(session, workspace):
    ticket = _ticket(session, workspace.id, "LG-1")
    _block(session, ticket)
    session.add(
        Approval(
            ticket_id=ticket.id,
            workspace_id=workspace.id,
            title="Rotate the deploy key",
            status=ApprovalStatus.PENDING,
        )
    )
    session.commit()

    assert resolve_repair_route(session, ticket) is None


def test_a_locked_state_is_the_operators_decision(session, workspace):
    ticket = _ticket(session, workspace.id, "LG-1")
    _block(session, ticket)
    ticket.state_locked = True
    session.add(ticket)
    session.commit()

    assert resolve_repair_route(session, ticket) is None


def test_a_failed_stage_run_with_budget_left_is_repairable(session, workspace):
    from loregarden.models.domain import AgentRun, RunStatus

    ticket = _ticket(session, workspace.id, "LG-1")
    _block(session, ticket, message="Agent run failed.")
    session.add(
        AgentRun(
            run_code="run-1",
            ticket_id=ticket.id,
            workspace_id=workspace.id,
            agent_id="implementer",
            stage_key="implement",
            status=RunStatus.FAILED,
        )
    )
    session.commit()

    assert resolve_repair_route(session, ticket) is RepairRoute.STAGE_RETRY


def test_a_declared_blocker_is_not_repairable(session, workspace):
    """An agent that finished its turn and said the work cannot proceed has
    answered the question. Re-running it buys the same answer for a whole turn."""
    from loregarden.models.domain import AgentRun, RunStatus

    ticket = _ticket(session, workspace.id, "LG-1")
    _block(session, ticket, message="The upstream API this needs has been retired.")
    session.add(
        AgentRun(
            run_code="run-1",
            ticket_id=ticket.id,
            workspace_id=workspace.id,
            agent_id="implementer",
            stage_key="implement",
            status=RunStatus.SUCCEEDED,
        )
    )
    session.commit()

    assert resolve_repair_route(session, ticket) is None


def test_a_hold_gives_up_its_lane_when_the_ticket_restarts_elsewhere(lanes, session, workspace):
    """Startup runs the repair driver and the interruption resume, and that one
    reserves a lane of its own. Whichever goes second must not put the ticket in
    a second lane — the defect 645 exists to prevent."""
    ticket = _ticket(session, workspace.id, "LG-1")
    _block(session, ticket)
    entry = _finish_blocked(lanes, session, ticket)

    # Something else restarted the ticket in lane 3 while the hold sat in lane 1.
    other = _orch(session, ticket, "resumed")
    slot3 = _slot(session, 3)
    slot3.is_available = False
    slot3.current_orchestration_run_id = other.id
    session.add(slot3)
    session.commit()

    lanes.reconcile_lanes()

    session.refresh(entry)
    assert entry.status == QueuePosition.STARTED
    assert _slot(session, 1).is_available is True
    # No second dispatch: the ticket is already running.
    assert lanes.dispatcher.launched == [ticket.id]
