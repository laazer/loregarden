"""Shutdown stops starting work, then waits a bounded time for what is running.

There was no drain. A restart — and backend edits *require* one, so they are
frequent by design — took every in-flight turn with it and left recovery to pick
up the pieces. `ReloadBlockedError` looked like a drain and was not: it refused
the self-improve sentinel reload while runs were in flight, which covers one
restart trigger and says nothing about a crash, a `task dev` cycle, or SIGTERM.

The rule these pin is the one that keeps a drain from making things worse:
**drain improves the good case and must not change the bad one.** Work that
misses the window is interrupted exactly as it is today, so recovery has one
path rather than two.

No test here sleeps against the real bound. The window is passed in, and the
"window closed" case uses a bound of zero rather than a wait that races it.
"""

from __future__ import annotations

import os
from unittest.mock import patch

import pytest
from loregarden.models.domain import (
    AgentRun,
    ExternalHarness,
    QueuedRun,
    QueuePosition,
    RunStatus,
    Ticket,
    WorkItemType,
)
from loregarden.services.drain import (
    DRAIN_REFUSED_REASON,
    begin_drain,
    end_drain,
    in_flight_runs,
    is_draining,
    wait_for_quiescence,
)
from loregarden.services.orchestration import OrchestrationService
from loregarden.services.queue_lanes import QueueLaneService
from loregarden.services.run_service import fail_interrupted_runs
from loregarden.services.ticket_service import TicketService
from sqlmodel import select


@pytest.fixture(autouse=True)
def _never_leave_the_process_draining():
    """A leaked flag would refuse work in every test after this module."""
    yield
    end_drain()


@pytest.fixture(name="ticket")
def ticket_fixture(db_session) -> Ticket:
    return TicketService(db_session).create_ticket(
        workspace_slug="loregarden",
        title="shutdown drain",
        work_item_type=WorkItemType.MILESTONE,
    )


def _running_run(db_session, ticket: Ticket, code: str = "run_live") -> AgentRun:
    run = AgentRun(
        run_code=code,
        ticket_id=ticket.id,
        workspace_id=ticket.workspace_id,
        agent_id="backend_implementer",
        status=RunStatus.RUNNING,
    )
    db_session.add(run)
    db_session.commit()
    db_session.refresh(run)
    return run


# ---- nothing new starts -------------------------------------------------


def test_dispatch_is_refused_while_draining(db_session, ticket):
    """The first promise: a turn the process is about to abandon never starts."""
    from loregarden.services import run_service

    begin_drain()
    with patch.object(run_service, "execute_agent_run_background") as spawn:
        run_service.schedule_agent_run("any-run-id")

    spawn.assert_not_called()


def test_orchestration_is_refused_while_draining(db_session, ticket):
    from loregarden.services import run_service

    begin_drain()
    with patch.object(run_service, "execute_orchestration_background") as spawn:
        run_service.schedule_orchestration(ticket.id)

    spawn.assert_not_called()


def test_a_refused_lane_entry_keeps_its_place_and_says_why(db_session, ticket):
    """Refusal is not failure.

    The entry is still queued — the next process starts it — but an operator
    reading the board sees why nothing is moving rather than an entry that looks
    stuck for no reason.
    """
    lanes = QueueLaneService(db_session, max_concurrent=3)
    lanes.slots.initialize_slots()

    # Drained *before* the add, which is the real sequence: shutdown begins and
    # requests keep arriving for a moment. `add_to_lane` starts the head itself
    # when the lane is idle, so adding first would start the very run this is
    # about.
    begin_drain()
    result = lanes.add_to_lane(ticket_id=ticket.id, slot_number=1, entry_kind="orchestration")

    assert result["status"] == "queued"
    db_session.expire_all()
    entry = db_session.exec(select(QueuedRun).where(QueuedRun.ticket_id == ticket.id)).first()
    assert entry.status in (QueuePosition.QUEUED, QueuePosition.SCHEDULED)
    assert entry.failure_reason == DRAIN_REFUSED_REASON


def test_a_drained_lane_does_not_hold_a_slot(db_session, ticket):
    """Refusing before the claim, so shutdown does not strand a lane."""
    from loregarden.models.domain import AgentSlot

    lanes = QueueLaneService(db_session, max_concurrent=3)
    lanes.slots.initialize_slots()

    begin_drain()
    lanes.add_to_lane(ticket_id=ticket.id, slot_number=1, entry_kind="orchestration")

    db_session.expire_all()
    slot = db_session.exec(select(AgentSlot).where(AgentSlot.slot_number == 1)).one()
    assert slot.is_available is True


# ---- what is running gets a bounded chance ------------------------------


def test_an_idle_process_exits_without_waiting(db_session):
    """Nothing in flight is the common case; it must cost nothing."""
    report = wait_for_quiescence(timeout_seconds=30)

    assert report.clean is True
    assert report.started_with == 0
    assert report.waited_seconds == 0.0


def test_the_window_is_bounded(db_session, ticket):
    """A run that will not finish must not hold the process open.

    Bound of zero rather than a short sleep: the point is that the wait ends,
    and a test that raced a real bound would be the flake this ticket's own
    description warns against.
    """
    _running_run(db_session, ticket)

    report = wait_for_quiescence(timeout_seconds=0)

    assert report.clean is False
    assert report.remaining == 1
    # The outcome alone does not pin boundedness: a wait that ignored the window
    # entirely would still end up "not clean" with one run left. This is an
    # upper bound with two orders of magnitude of margin, not a timing race.
    assert report.waited_seconds < 2.0, (
        f"a zero-second window waited {report.waited_seconds:.1f}s — the bound is not respected"
    )


def test_a_run_that_lands_inside_the_window_is_waited_for(db_session, ticket):
    """The good case the drain exists for, made deterministic.

    The run finishes on the first poll, so this measures that the wait *ends*
    when the work lands rather than how long it happens to take.
    """
    run = _running_run(db_session, ticket)

    def _finish_it(session):
        stored = session.get(AgentRun, run.id)
        if stored is not None and stored.status is RunStatus.RUNNING:
            stored.status = RunStatus.SUCCEEDED
            session.add(stored)
            session.commit()
        return []

    with patch("loregarden.services.drain.in_flight_runs", side_effect=_finish_it):
        report = wait_for_quiescence(timeout_seconds=30, poll_seconds=0.01)

    assert report.clean is True


def test_queued_runs_are_not_waited_for(db_session, ticket):
    """Nothing started them, so there is nothing to land."""
    run = _running_run(db_session, ticket)
    run.status = RunStatus.QUEUED
    db_session.add(run)
    db_session.commit()

    assert in_flight_runs(db_session) == []


# ---- only runs this process can judge hold the window -------------------


#: A pid the tests declare dead. `pid_alive` is patched wherever this is used, so
#: the number never reaches the OS — asking the real kernel about an arbitrary pid
#: is a race with pid reuse, not a test.
_DEAD_PID = 4_242_424


def _external_run(db_session, ticket: Ticket, code: str = "run_harness") -> AgentRun:
    """A stage checked out to a harness in somebody else's terminal."""
    run = _running_run(db_session, ticket, code)
    run.external_harness = ExternalHarness.CLAUDE_CODE
    db_session.add(run)
    db_session.commit()
    db_session.refresh(run)
    return run


def test_a_run_this_process_cannot_judge_is_not_waited_for(db_session, ticket):
    """No pid here and no lease renewer: nothing about this run is ours to wait on.

    `run_has_renewer` is False for an external-harness run, so its RUNNING row is
    not evidence of anything this process is doing — and it is routinely far
    longer-lived than the window: 97 of 128 completed external runs outran the
    20s bound, the longest by three hours. Counting it makes every shutdown
    spend the full timeout and then log a warning about work that was never here.
    """
    _external_run(db_session, ticket)

    assert in_flight_runs(db_session) == []


def test_an_external_run_alone_does_not_hold_the_window_open(db_session, ticket):
    """The behavioural half: an idle process stays an idle process."""
    _external_run(db_session, ticket)

    report = wait_for_quiescence(timeout_seconds=5)

    assert report.clean is True
    assert report.started_with == 0
    assert report.waited_seconds == 0.0


def test_an_in_process_run_still_holds_the_window(db_session, ticket):
    """The bad case must not change. Only the unjudgeable run is dropped.

    A supervised run is still waited for, still counted, and still left to the
    interruption path when the window closes — with an external run sitting
    beside it to show the filter is about judgeability, not about draining less.
    """
    _running_run(db_session, ticket, "run_supervised")
    _external_run(db_session, ticket)

    report = wait_for_quiescence(timeout_seconds=0)

    assert report.started_with == 1
    assert report.remaining == 1
    assert report.clean is False


def test_a_handoff_run_with_a_live_pid_still_counts(db_session, ticket):
    """A pid in this process is evidence the filter asks for.

    A terminal handoff stamps the shell pid it was pasted into. It is supervised
    either way — a handoff goes through the ordinary dispatch path, so its
    `external_harness` is null and `run_has_renewer` is already True — so this
    pins the pid half only for a run the renewer half would keep anyway. The
    external-plus-pid case below is the one that separates the two rules.
    """
    run = _running_run(db_session, ticket, "run_handoff")
    run.handoff_pid = os.getpid()
    db_session.add(run)
    db_session.commit()

    assert [r.id for r in in_flight_runs(db_session)] == [run.id]


def test_an_external_run_with_a_live_pid_here_still_counts(db_session, ticket):
    """The rule is "no pid here AND no renewer", not "external runs do not count".

    Written as a blanket `external_harness is not None` exclusion, the filter
    drops a run whose process this machine can see — the one piece of evidence
    that settles the question outright, and the reason `pid_alive` exists. The
    two conditions are separate (`stage_retry_budget` spells the same predicate
    `run_has_renewer(run) or run.handoff_pid is not None`), and a filter that
    collapses them reads as correct against every other test in this file.
    """
    run = _external_run(db_session, ticket, "run_external_with_pid")
    run.handoff_pid = os.getpid()
    db_session.add(run)
    db_session.commit()

    assert [r.id for r in in_flight_runs(db_session)] == [run.id]


def test_what_the_drain_leaves_behind_is_what_the_boot_sweep_will_settle(db_session, ticket):
    """AC5 without asserting the log's copy.

    The timeout warns that the runs still in flight "will be settled by the
    interruption path". Pinning that sentence would pin prose; the claim behind
    it is checkable — but the unscoped boot reaper exempts external-harness
    runs, so a plain "every counted run is claimed by the sweep" is not the
    guarantee the code can keep, and the earlier version of this test passed
    only because it never built the corner where the two disagree.

    The guarantee that is true, and the one asserted here: every counted run is
    either claimed by the boot sweep, or has a live pid on this host — a harness
    genuinely working, deliberately left alone, settled through the lease by
    `settle_expired_agent_runs` once that pid goes away. Nothing is counted with
    no path to settlement.

    All four corners, since only one of them leaked: in-process (counted,
    claimed), external with no pid (not counted), external with a live pid
    (counted, exempt, alive), external with a dead pid — which used to be
    counted, spending the whole window on a process that was gone and then
    promising a sweep that exempts it.
    """
    supervised = OrchestrationService(db_session).start_run(ticket)
    pidless = _external_run(db_session, ticket, "run_external_pidless")
    dead = _external_run(db_session, ticket, "run_external_dead_pid")
    dead.handoff_pid = _DEAD_PID
    live = _external_run(db_session, ticket, "run_external_live_pid")
    live.handoff_pid = os.getpid()
    for run in (pidless, dead, live):
        run.stage_key = supervised.stage_key
        db_session.add(run)
    db_session.commit()

    with patch("loregarden.services.drain.pid_alive", side_effect=lambda pid: pid == os.getpid()):
        waited_for = {r.id for r in in_flight_runs(db_session)}
    settled = {r.id for r in fail_interrupted_runs(db_session)}

    assert supervised.id in waited_for
    assert pidless.id not in waited_for
    assert dead.id not in waited_for, (
        "a provably dead process held the window open, and no sweep will claim it"
    )
    assert waited_for - settled == {live.id}, (
        "the drain counted a run the interruption path exempts and no live process "
        "backs, so the timeout log promises a settlement that never comes"
    )


# ---- the flag must not outlive the app that set it ----------------------


def test_a_torn_down_app_leaves_the_process_undrained(isolated_db):
    """The bug this shipped with, and the reason it was invisible.

    The lifespan sets the flag on the way out — right for a process that then
    exits, wrong for one that keeps running. A test process builds and tears
    down many apps in one interpreter, so a flag left set refused work in every
    test that followed: 42 failures across the suite.

    The client is built and exited *inside* the test rather than taken as a
    fixture. A fixture tears down after the test body, so asserting on the
    fixture would check the flag before the lifespan that sets it has run —
    which is how the first version of this test passed against the bug.
    """
    from fastapi.testclient import TestClient
    from loregarden.main import app

    with TestClient(app):
        pass

    assert is_draining() is False, "the app left the whole process refusing new work"


# ---- the flag itself ----------------------------------------------------


def test_draining_is_idempotent_and_reversible(db_session):
    """`end_drain` exists for the reload path, which drains and then stays up."""
    assert is_draining() is False
    begin_drain()
    begin_drain()
    assert is_draining() is True
    end_drain()
    assert is_draining() is False
