"""New work is held back, for a limited time, so a landed change can be reloaded.

`follow_main` waits for a moment with nothing in flight. With several tickets
being driven, the next stage and the next lane entry kept filling it: at the 90th
percentile of 136 landings that moment was 5 hours away.
"""

from __future__ import annotations

from collections.abc import Iterator
from unittest import mock

import pytest
from loregarden.config import settings
from loregarden.models.domain import (
    AgentRun,
    AgentSlot,
    ExternalHarness,
    OrchestrationDriver,
    OrchestrationRun,
    OrchestrationRunStatus,
    QueuedRun,
    QueuePosition,
    RunStatus,
    Ticket,
    TicketState,
    Workspace,
)
from loregarden.services import reload_hold, reload_yield
from loregarden.services.builtin_orchestrator import BuiltinOrchestrator
from loregarden.services.queue_lanes import QueueLaneService
from loregarden.services.reload_yield import RELOAD_YIELD_MESSAGE, Continuation
from loregarden.services.self_improve_restart import evaluate_reload_readiness
from sqlmodel import Session, col, select
from tests.factories import make_agent_run
from tests.test_auto_mode_subtree import _make_ticket, _make_workspace, _profile

AFTER = settings.follow_main_hold_after_seconds
HOLD = settings.follow_main_hold_seconds
COOLDOWN = settings.follow_main_hold_cooldown_seconds


class Clock:
    def __init__(self) -> None:
        self.now = 1_000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture(autouse=True)
def _forget_the_hold() -> Iterator[None]:
    reload_hold.reset()
    yield
    reload_hold.reset()


@pytest.fixture(name="clock")
def clock_fixture() -> Iterator[Clock]:
    clock = Clock()
    with mock.patch.object(reload_hold, "_now", clock):
        yield clock


def _hold(clock: Clock) -> None:
    reload_hold.note_waiting([])
    clock.now += AFTER
    assert reload_hold.note_waiting([]).holding


# ---- when the hold is on ------------------------------------------------


def test_a_change_waits_for_an_idle_moment_before_anything_is_held(clock: Clock):
    assert not reload_hold.note_waiting([]).holding
    clock.now += AFTER - 1
    assert not reload_hold.note_waiting([]).holding
    assert not reload_hold.holds_starts()

    clock.now += 1

    assert reload_hold.note_waiting([]).holding
    assert reload_hold.holds_starts()


def test_the_wait_is_measured_from_the_first_change_that_waited(clock: Clock):
    """A later landing does not restart the wait, or a busy main would never hold."""
    reload_hold.note_waiting([])
    clock.now += AFTER / 2
    reload_hold.note_waiting([])
    clock.now += AFTER / 2

    assert reload_hold.note_waiting([]).holding


def test_a_hold_ends_on_its_own_deadline_with_no_tick_to_end_it(clock: Clock):
    _hold(clock)
    clock.now += HOLD - 1
    assert reload_hold.holds_starts()

    clock.now += 1

    assert not reload_hold.holds_starts()


def test_ticks_during_a_hold_do_not_extend_it(clock: Clock):
    _hold(clock)
    clock.now += HOLD / 2
    reload_hold.note_waiting([])
    clock.now += HOLD / 2

    assert not reload_hold.holds_starts()
    assert not reload_hold.note_waiting([]).holding


def test_a_hold_that_did_not_work_is_not_tried_again_until_the_cooldown_passes(clock: Clock):
    _hold(clock)
    clock.now += HOLD
    assert not reload_hold.note_waiting([]).holding

    clock.now += COOLDOWN - 1
    assert not reload_hold.note_waiting([]).holding

    clock.now += 1
    assert reload_hold.note_waiting([]).holding


def test_the_cooldown_outlives_a_change_that_settled(clock: Clock):
    _hold(clock)
    clock.now += HOLD
    reload_hold.note_waiting([])
    reload_hold.note_settled()

    reload_hold.note_waiting([])
    clock.now += AFTER

    assert not reload_hold.note_waiting([]).holding


def test_nothing_is_held_for_work_that_cannot_yield(clock: Clock):
    reload_hold.note_waiting([])
    clock.now += AFTER

    tick = reload_hold.note_waiting(["run_a waits for a person's approval"])

    assert not tick.holding
    assert "run_a" in tick.detail
    assert not reload_hold.holds_starts()


def test_a_hold_ends_when_something_stops_yielding_and_the_wait_starts_again(clock: Clock):
    _hold(clock)

    assert not reload_hold.note_waiting(["run_a waits for a person's approval"]).holding
    assert not reload_hold.holds_starts()
    assert not reload_hold.note_waiting([]).holding

    clock.now += AFTER
    assert reload_hold.note_waiting([]).holding


def test_settling_ends_the_hold_and_the_wait(clock: Clock):
    _hold(clock)

    reload_hold.note_settled()

    assert not reload_hold.holds_starts()
    assert not reload_hold.note_waiting([]).holding


def test_zero_seconds_never_holds(clock: Clock):
    with mock.patch.object(settings, "follow_main_hold_seconds", 0.0):
        reload_hold.note_waiting([])
        clock.now += AFTER
        assert not reload_hold.note_waiting([]).holding


def test_the_lanes_stay_held_while_a_ticket_yields_past_the_deadline(clock: Clock):
    _hold(clock)

    with reload_hold.yielding() as stops:
        assert stops
        clock.now += HOLD
        assert reload_hold.holds_starts()

    assert not reload_hold.holds_starts()


def test_a_boundary_with_no_hold_holds_no_lane(clock: Clock):
    with reload_hold.yielding() as stops:
        assert not stops
        assert not reload_hold.holds_starts()


# ---- what a hold cannot stop --------------------------------------------


@pytest.fixture(name="workspace")
def workspace_fixture(db_session: Session, tmp_path) -> Workspace:
    return _make_workspace(db_session, tmp_path, "reload-hold")


@pytest.fixture(name="ticket")
def ticket_fixture(db_session: Session, workspace: Workspace) -> Ticket:
    return _make_ticket(db_session, workspace, external_id="rh-1", title="Held")


def _orchestration(
    session: Session,
    ticket: Ticket,
    driver: OrchestrationDriver = OrchestrationDriver.BUILTIN_AUTOPILOT,
) -> OrchestrationRun:
    run = OrchestrationRun(
        run_code=f"orch_{ticket.external_id}",
        ticket_id=ticket.id,
        workspace_id=ticket.workspace_id,
        driver=driver,
        status=OrchestrationRunStatus.RUNNING,
    )
    session.add(run)
    session.commit()
    session.refresh(run)
    return run


def _cannot_yield(session: Session) -> list[str]:
    with mock.patch("loregarden.services.self_improve_restart._workspace_or_error"):
        return evaluate_reload_readiness(session)["cannot_yield"]


def test_an_agent_the_server_runs_can_yield(db_session: Session, ticket: Ticket):
    make_agent_run(
        db_session,
        workspace_id=ticket.workspace_id,
        ticket_id=ticket.id,
        status=RunStatus.RUNNING,
    )
    _orchestration(db_session, ticket)

    assert _cannot_yield(db_session) == []


@pytest.mark.parametrize(
    "shape",
    [
        {"status": RunStatus.AWAITING_PERMISSION},
        {"status": RunStatus.RUNNING, "external_harness": ExternalHarness.CLAUDE_CODE},
    ],
)
def test_an_agent_a_hold_cannot_stop_is_named(db_session: Session, ticket: Ticket, shape: dict):
    run = make_agent_run(db_session, workspace_id=ticket.workspace_id, ticket_id=ticket.id, **shape)

    (reason,) = _cannot_yield(db_session)

    assert run.run_code in reason


def test_a_terminal_handoff_is_named(db_session: Session, ticket: Ticket):
    run = make_agent_run(
        db_session, workspace_id=ticket.workspace_id, ticket_id=ticket.id, status=RunStatus.RUNNING
    )
    run.handoff_accepted_at = run.created_at
    db_session.add(run)
    db_session.commit()

    (reason,) = _cannot_yield(db_session)

    assert run.run_code in reason


def test_an_orchestration_driven_from_outside_is_named(db_session: Session, ticket: Ticket):
    run = _orchestration(db_session, ticket, driver=OrchestrationDriver.EXTERNAL_MCP)

    (reason,) = _cannot_yield(db_session)

    assert run.run_code in reason


# ---- lanes --------------------------------------------------------------


@pytest.fixture(name="lanes")
def lanes_fixture(db_session: Session) -> QueueLaneService:
    lanes = QueueLaneService(db_session, max_concurrent=3)
    lanes.slots.initialize_slots()
    dispatcher = mock.Mock()
    dispatcher.dispatch_orchestration.side_effect = lambda ticket, **_: _orchestration(
        db_session, ticket
    )
    lanes.dispatcher = dispatcher
    return lanes


def _entries(session: Session, ticket: Ticket) -> list[QueuedRun]:
    return list(
        session.exec(
            select(QueuedRun)
            .where(QueuedRun.ticket_id == ticket.id)
            .where(QueuedRun.status == QueuePosition.QUEUED)
        ).all()
    )


def test_a_held_lane_keeps_its_entry_and_says_why_then_starts_it(
    db_session: Session, lanes: QueueLaneService, ticket: Ticket, clock: Clock
):
    _hold(clock)

    assert lanes.add_to_lane(ticket_id=ticket.id, slot_number=1)["status"] == "queued"

    (entry,) = _entries(db_session, ticket)
    assert entry.failure_reason == reload_hold.RELOAD_HOLD_REASON
    lanes.dispatcher.dispatch_orchestration.assert_not_called()
    assert db_session.exec(select(AgentSlot).where(AgentSlot.slot_number == 1)).one().is_available

    reload_hold.note_settled()
    lanes.reconcile_lanes()

    db_session.refresh(entry)
    assert entry.status is QueuePosition.ACTIVE
    assert entry.failure_reason == "", "the history would show it as why the run ended"


# ---- a ticket being driven ----------------------------------------------


def _runs(session: Session, ticket: Ticket) -> list[str]:
    rows = session.exec(
        select(AgentRun).where(AgentRun.ticket_id == ticket.id).order_by(col(AgentRun.created_at))
    ).all()
    return [run.stage_key for run in rows]


def _hold_once_a_stage_ran(clock: Clock, stage_key: str = "work"):
    """The hold comes on while the ticket's ``stage_key`` runs."""
    real = BuiltinOrchestrator._dispatch_agent_stage

    def dispatch(self, ticket, orch_run, stage_def, *args, **kwargs):
        stopped = real(self, ticket, orch_run, stage_def, *args, **kwargs)
        if stage_def.key == stage_key and not reload_hold.holds_starts():
            _hold(clock)
        return stopped

    return mock.patch.object(BuiltinOrchestrator, "_dispatch_agent_stage", dispatch)


def test_a_driven_ticket_stops_before_its_next_stage_and_is_queued_to_continue(
    db_session: Session, ticket: Ticket, clock: Clock
):
    with _hold_once_a_stage_ran(clock):
        run = BuiltinOrchestrator(db_session).execute(
            ticket,
            _profile(),
            auto_approve=True,
            approve_design_plans=False,
            auto_repair=False,
            stop_at_stage_key="review",
            timeout_seconds=77,
            max_stages=5,
        )

    assert _runs(db_session, ticket) == ["work"]
    assert run.status is OrchestrationRunStatus.SUCCEEDED
    assert run.error_message == RELOAD_YIELD_MESSAGE
    db_session.refresh(ticket)
    assert ticket.state is TicketState.IN_PROGRESS
    (entry,) = _entries(db_session, ticket)
    assert entry.position == 1
    assert (entry.auto_approve, entry.approve_design_plans, entry.auto_repair) == (
        True,
        False,
        False,
    )
    assert entry.stop_at_stage_key == "review"
    assert entry.timeout_seconds == 77
    assert entry.max_stages == 4
    assert entry.driver == OrchestrationDriver.BUILTIN_AUTOPILOT.value


def test_a_yielded_ticket_carries_on_from_the_stage_it_stopped_before(
    db_session: Session, ticket: Ticket, clock: Clock
):
    with _hold_once_a_stage_ran(clock):
        BuiltinOrchestrator(db_session).execute(ticket, _profile(), auto_approve=True)
    reload_hold.reset()

    BuiltinOrchestrator(db_session).execute(ticket, _profile(), auto_approve=True)

    db_session.refresh(ticket)
    assert _runs(db_session, ticket) == ["work", "signoff"]
    assert ticket.state is TicketState.DONE


def test_the_server_is_idle_once_the_ticket_has_yielded(
    db_session: Session, ticket: Ticket, clock: Clock
):
    with _hold_once_a_stage_ran(clock):
        BuiltinOrchestrator(db_session).execute(ticket, _profile(), auto_approve=True)

    with mock.patch("loregarden.services.self_improve_restart._workspace_or_error"):
        assert evaluate_reload_readiness(db_session)["blockers"] == []


def test_with_no_hold_the_ticket_runs_through(db_session: Session, ticket: Ticket):
    BuiltinOrchestrator(db_session).execute(ticket, _profile(), auto_approve=True)

    db_session.refresh(ticket)
    assert ticket.state is TicketState.DONE
    assert _entries(db_session, ticket) == []


def test_the_landing_waits_too(db_session: Session, ticket: Ticket, clock: Clock):
    """The terminal stage runs the tree's verify suite, which takes minutes."""
    with _hold_once_a_stage_ran(clock, "signoff"):
        BuiltinOrchestrator(db_session).execute(ticket, _profile(), auto_approve=True)

    db_session.refresh(ticket)
    assert _runs(db_session, ticket) == ["work", "signoff"]
    assert ticket.state is TicketState.IN_PROGRESS
    (entry,) = _entries(db_session, ticket)
    assert entry.max_stages is None


def test_a_ticket_at_its_stage_limit_ends_there_and_is_not_queued_past_it(
    db_session: Session, ticket: Ticket, clock: Clock
):
    with _hold_once_a_stage_ran(clock):
        run = BuiltinOrchestrator(db_session).execute(
            ticket, _profile(), auto_approve=True, max_stages=1
        )

    assert _runs(db_session, ticket) == ["work"]
    assert run.error_message != RELOAD_YIELD_MESSAGE
    assert _entries(db_session, ticket) == []


def test_the_continuation_goes_ahead_of_what_waits_in_its_own_lane(
    db_session: Session,
    lanes: QueueLaneService,
    workspace: Workspace,
    ticket: Ticket,
    clock: Clock,
):
    run = _orchestration(db_session, ticket)
    slot = db_session.exec(select(AgentSlot).where(AgentSlot.slot_number == 2)).one()
    slot.is_available = False
    slot.current_orchestration_run_id = run.id
    db_session.add(slot)
    db_session.commit()
    behind = _make_ticket(db_session, workspace, external_id="rh-2", title="Behind")
    lanes.add_to_lane(ticket_id=behind.id, slot_number=2)

    reload_yield.queue_continuation(
        db_session, run, ticket, Continuation(nested=False, max_stages=None)
    )

    waiting = lanes.waiting_in_lane(2)
    assert [(e.ticket_id, e.position) for e in waiting] == [(ticket.id, 1), (behind.id, 2)]


def test_a_lane_cannot_adopt_the_run_that_is_yielding(
    db_session: Session, ticket: Ticket, clock: Clock
):
    """The hold expires between queueing the continuation and ending the run.

    A run that holds no lane leaves one free: starting the continuation there
    would bind it to this run, which then ends, and the ticket would stop.
    """
    real = reload_yield.queue_continuation

    def queue_then_expire(*args, **kwargs):
        entry = real(*args, **kwargs)
        clock.now += HOLD
        QueueLaneService(db_session).reconcile_lanes()
        return entry

    with (
        _hold_once_a_stage_ran(clock),
        mock.patch(
            "loregarden.services.builtin_orchestrator.queue_continuation", queue_then_expire
        ),
    ):
        BuiltinOrchestrator(db_session).execute(ticket, _profile(), auto_approve=True)

    (entry,) = _entries(db_session, ticket)
    assert entry.orchestration_run_id is None


def test_a_stopped_child_stops_its_parent_and_only_the_parent_is_queued(
    db_session: Session, workspace: Workspace, ticket: Ticket, clock: Clock
):
    middle = _make_ticket(
        db_session, workspace, external_id="rh-m", title="Middle", parent_ticket_id=ticket.id
    )
    leaf = _make_ticket(
        db_session, workspace, external_id="rh-l", title="Leaf", parent_ticket_id=middle.id
    )

    with _hold_once_a_stage_ran(clock):
        run = BuiltinOrchestrator(db_session).execute(ticket, _profile(), auto_approve=True)

    assert run.error_message == RELOAD_YIELD_MESSAGE
    assert _runs(db_session, leaf) == ["work"]
    assert len(_entries(db_session, ticket)) == 1
    assert _entries(db_session, middle) == _entries(db_session, leaf) == []

    reload_hold.reset()
    BuiltinOrchestrator(db_session).execute(ticket, _profile(), auto_approve=True)
    db_session.refresh(ticket)
    assert ticket.state is TicketState.DONE
    assert _runs(db_session, leaf) == ["work", "signoff"]


def test_a_parent_whose_child_yielded_is_queued_even_if_the_hold_has_ended(
    db_session: Session, workspace: Workspace, ticket: Ticket, clock: Clock
):
    child = _make_ticket(
        db_session, workspace, external_id="rh-c", title="Child", parent_ticket_id=ticket.id
    )
    real = BuiltinOrchestrator._yield_to_reload

    def yield_then_expire(self, orch_run: OrchestrationRun, *args, **kwargs):
        settled = real(self, orch_run, *args, **kwargs)
        if settled is not None and orch_run.ticket_id == child.id:
            reload_hold.note_settled()
        return settled

    with (
        _hold_once_a_stage_ran(clock),
        mock.patch.object(BuiltinOrchestrator, "_yield_to_reload", yield_then_expire),
    ):
        BuiltinOrchestrator(db_session).execute(ticket, _profile(), auto_approve=True)

    assert not reload_hold.holds_starts()
    queued = db_session.exec(select(QueuedRun).where(QueuedRun.ticket_id == ticket.id)).all()
    assert len(queued) == 1


@pytest.mark.parametrize(
    ("max_stages", "stages_run", "left"), [(None, 3, None), (0, 3, 0), (5, 3, 2), (3, 3, 1)]
)
def test_a_used_up_stage_limit_does_not_become_no_limit(max_stages, stages_run, left):
    continuation = Continuation.of(nested=False, max_stages=max_stages, stages_run=stages_run)

    assert continuation.max_stages == left


def test_a_stage_recovered_from_an_interruption_runs_before_the_ticket_yields(
    db_session: Session, ticket: Ticket, clock: Clock
):
    """The block that marks it as a resume is already cleared, so a later pass would
    start a parallel stage's finished members again."""
    _hold(clock)
    real = BuiltinOrchestrator._resolve_stages_with_recovery
    recovered = iter(["work"])

    def recover(self, ticket):
        instance, stages, _ = real(self, ticket)
        return instance, stages, next(recovered, None)

    with mock.patch.object(BuiltinOrchestrator, "_resolve_stages_with_recovery", recover):
        BuiltinOrchestrator(db_session).execute(ticket, _profile(), auto_approve=True)

    assert _runs(db_session, ticket) == ["work"]
    assert len(_entries(db_session, ticket)) == 1
