"""Repair on a clock, not on a restart and not on being watched.

Eleven ordered sweeps ran in exactly one place — the startup lifespan — so a
lane wedged at 09:05 stayed wedged until someone restarted the server. One sweep
escaped that and made it worse: `reconcile_lanes` also ran on the queue status
read path, and the dashboard polls it every few seconds, so lane repair fired
continuously *while someone was watching* and never otherwise. The failure could
only survive while nobody was looking at it.

What these pin is the part that is dangerous to get wrong. A timer that repairs
is only worth having if it cannot mistake live work for wreckage, so the sweeps
that assume a fresh process — the `fail_interrupted_*` reapers — must stay out
of it until 435 gives `agent_runs` a heartbeat that can disprove RUNNING.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from unittest.mock import patch

from loregarden.models.domain import (
    AgentRun,
    AgentSlot,
    OrchestrationRun,
    OrchestrationRunStatus,
    RunStatus,
    StageStatus,
    Ticket,
    WorkItemType,
)
from loregarden.services import reconciliation
from loregarden.services.reconcile_timer import run_reconcile_loop, start_reconcile_loop
from loregarden.services.reconciliation import PERIODIC_STEPS, SweepStep, reconcile_once
from loregarden.services.ticket_service import TicketService
from sqlmodel import select

#: The sweeps that assume every in-flight row belongs to a process that just
#: died. True exactly once, at boot.
_RESTART_ONLY = (
    "fail_interrupted_runs",
    "fail_interrupted_orchestration_runs",
    "fail_interrupted_triage_turns",
    "fail_interrupted_branch_triage_turns",
    "fail_interrupted_baxter_chat_turns",
    "fail_interrupted_studio_turns",
    "fail_interrupted_asides",
    "resume_interrupted_orchestrations",
    "reconcile_worktrees",
)


def _ticket(db_session, title: str) -> Ticket:
    return TicketService(db_session).create_ticket(
        workspace_slug="loregarden",
        title=title,
        work_item_type=WorkItemType.MILESTONE,
    )


# ---- what the timer must never touch -----------------------------------


def test_the_periodic_pass_excludes_the_restart_reapers():
    """A reaper on a timer kills live work.

    `fail_interrupted_runs` fails every in-flight run with no liveness test —
    correct for a process that has just restarted, catastrophic on a clock. 437
    requires the timer skip it until 435 delivers a predicate that can tell a
    live run from an orphaned one. This fails the moment one is added.
    """
    scheduled = {step.name for step in PERIODIC_STEPS}

    assert scheduled.isdisjoint(_RESTART_ONLY), (
        f"restart-semantics sweep on the timer: {sorted(scheduled & set(_RESTART_ONLY))}"
    )


def test_a_healthy_orchestration_and_its_slot_survive_a_sweep(db_session):
    """The guarantee the whole ticket rests on.

    A sweep that cannot run against live work is a sweep nobody can afford to
    run often, which is the cadence being replaced.
    """
    ticket = _ticket(db_session, "reconcile timer — healthy in flight")
    orch = OrchestrationRun(
        ticket_id=ticket.id,
        workspace_id=ticket.workspace_id,
        run_code="orch_healthy",
        profile_slug="loregarden",
        status=OrchestrationRunStatus.RUNNING,
    )
    db_session.add(orch)
    db_session.commit()
    db_session.refresh(orch)

    run = AgentRun(
        run_code="run_healthy",
        ticket_id=ticket.id,
        workspace_id=ticket.workspace_id,
        agent_id="backend_implementer",
        status=RunStatus.RUNNING,
        orchestration_run_id=orch.id,
    )
    ticket.workflow_stage_status = StageStatus.RUNNING
    db_session.add_all([run, ticket])
    db_session.commit()

    slot = db_session.exec(select(AgentSlot).where(AgentSlot.slot_number == 1)).first()
    if slot is None:
        slot = AgentSlot(slot_number=1)
        db_session.add(slot)
    slot.is_available = False
    slot.current_orchestration_run_id = orch.id
    db_session.add(slot)
    db_session.commit()

    assert reconcile_once(db_session) == []

    db_session.expire_all()
    db_session.refresh(orch)
    db_session.refresh(run)
    held = db_session.exec(select(AgentSlot).where(AgentSlot.slot_number == 1)).one()

    assert orch.status == OrchestrationRunStatus.RUNNING, "the sweep settled a live orchestration"
    assert run.status == RunStatus.RUNNING, "the sweep failed a live agent run"
    assert held.is_available is False, "the sweep freed a slot that was in use"
    assert held.current_orchestration_run_id == orch.id


# ---- one bad step does not stop the rest -------------------------------


def test_a_failing_step_is_logged_and_the_others_still_run(db_session):
    """Best-effort, matching the shape reconcile_slots already had.

    A pass that aborts halfway leaves the system less consistent than one that
    never ran, and on the timer an exception would end the loop outright.
    """
    ran: list[str] = []

    def _boom(session):
        raise RuntimeError("sweep exploded")

    def _ok(session):
        ran.append("after")

    steps = (
        SweepStep("explodes", _boom),
        SweepStep("after", _ok),
    )
    with patch.object(reconciliation, "PERIODIC_STEPS", steps):
        failed = reconcile_once(db_session)

    assert failed == ["explodes"]
    assert ran == ["after"], "a failing step stopped the ones behind it"


def test_the_pass_never_raises(db_session):
    """Its callers are a boot sequence and a loop; neither can afford a throw."""

    def _boom(session):
        raise RuntimeError("sweep exploded")

    with patch.object(reconciliation, "PERIODIC_STEPS", (SweepStep("explodes", _boom),)):
        assert reconcile_once(db_session) == ["explodes"]


# ---- the clock ---------------------------------------------------------

#: Deadlock guard, not a timing budget. `_sweeps_until` resumes on the sweep
#: that reaches its target, so this bounds only the pathological case of a loop
#: that has stopped sweeping without ending — it is never what the assertions
#: below are waiting on, and a healthy or a dead loop both finish long before it.
_HANG_GUARD_SECONDS = 30.0


async def _sweeps_until(sweep: Callable[[], list[str]], target: int) -> tuple[list[int], bool]:
    """Run the timer's loop until it has swept `target` times, then cancel it.

    Nothing here waits on a duration. The interval is zero, so each iteration is
    a bare yield to the event loop, and the driver resumes on the sweep that
    hits the target — which makes the outcome the same on an idle machine and
    under `pytest -n auto`. Both predecessors were wall-clock races: the first
    slept 0.05s against a 0.01s interval and asserted two iterations had fitted
    inside it (6 of 20 runs saw only one, under load), and its replacement
    polled a ten-second ceiling, which made the race rarer without removing it.

    Returns the sweeps recorded, and whether the loop was still running at the
    moment it was cancelled — a loop that had already ended on its own is the
    regression these tests exist to catch.
    """
    loop = asyncio.get_running_loop()
    calls: list[int] = []
    reached = asyncio.Event()

    def _counted() -> list[str]:
        # Called on a worker thread: the loop invokes `_sweep` via
        # asyncio.to_thread, so the event has to be set back on the loop's own
        # thread rather than directly.
        calls.append(1)
        if len(calls) >= target:
            loop.call_soon_threadsafe(reached.set)
        return sweep()

    with patch("loregarden.services.reconcile_timer._sweep", _counted):
        task = asyncio.create_task(run_reconcile_loop(0))
        waiter = asyncio.create_task(reached.wait())
        # Racing the two means a loop that dies is noticed on the spot instead
        # of after the guard expires.
        await asyncio.wait(
            {task, waiter}, return_when=asyncio.FIRST_COMPLETED, timeout=_HANG_GUARD_SECONDS
        )
        still_running = not task.done()
        waiter.cancel()
        task.cancel()
        await asyncio.gather(task, waiter, return_exceptions=True)

    return calls, still_running


def test_the_loop_sweeps_repeatedly(db_session):
    """The whole point: repair happens again without anyone doing anything."""

    def _ok() -> list[str]:
        return []

    calls, still_running = asyncio.run(_sweeps_until(_ok, 2))

    assert len(calls) >= 2, f"expected repeated sweeps, got {len(calls)}"
    assert still_running, "the loop ended instead of waiting for its next tick"


def test_the_loop_survives_a_failing_sweep(db_session):
    """A bad pass must not end the cadence and quietly restore the old one."""

    def _explode() -> list[str]:
        raise RuntimeError("whole sweep exploded")

    calls, still_running = asyncio.run(_sweeps_until(_explode, 2))

    assert len(calls) >= 2, "the loop died on the first failing sweep"
    assert still_running, "the loop ended instead of waiting for its next tick"


def test_a_non_positive_interval_disables_the_timer():
    """The off switch, for a test process or an operator who wants boot-only."""

    async def _drive():
        return start_reconcile_loop(0)

    assert asyncio.run(_drive()) is None


# ---- the read path is a read ------------------------------------------


def test_the_queue_status_read_no_longer_repairs():
    """Repair must not correlate with someone having the dashboard open.

    Pinned against the source because the failure is an absence: a reintroduced
    call would restore the cadence silently, and every test here would pass.
    """
    from pathlib import Path

    source = Path(reconciliation.__file__).with_name("queue_status.py").read_text()

    assert "reconcile_lanes()" not in source
    assert "settle_expired_orchestration_leases(" not in source
