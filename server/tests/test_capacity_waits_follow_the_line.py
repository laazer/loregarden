"""Every in-process wait for capacity keeps its place while the line moves.

A gate once waited a fixed 30 minutes behind an orderly line of test suites, gave
its place up, and the transition was blocked as if its work were wrong. That fix
moved the gate onto `capacity_line.wait_while_line_moves` and left three callers on
the fixed budget. On 2026-11-06 a survey pass's agent run waited its 30 minutes
behind a moving line and its orchestration was blocked the same way, holding the
survey lane while nothing ran.

So each waiter is driven here through the same scripted line on a fake clock: a
lease ahead finishes at t=10, the rest free up at t=20, and the stall window is 15.
A fixed 15-second budget gives up at t=15; a wait that follows the line is granted
at t=20. A line where nothing finishes still gives up, naming who holds it.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta
from functools import partial
from uuid import uuid4

import pytest
from loregarden.models.domain import (
    CapacityPool,
    CliAdapter,
    DockerFootprint,
    DockerGrantState,
    DockerHolderKind,
    DockerLease,
    DockerLeaseEndReason,
    DockerLeaseStatus,
    OrchestrationRun,
    OrchestrationRunStatus,
    Ticket,
    TicketState,
    WorkflowStageDef,
)
from loregarden.models.domain.enums import utcnow
from loregarden.services import (
    capacity_line,
    docker_leases,
    orchestration_capacity,
    run_capacity,
    stage_docker_capacity,
)
from loregarden.services.capacity_activity import WORKING_HOLD_COUNTS_SECONDS
from loregarden.services.docker_leases import DockerReservation
from loregarden.services.run_capacity import HostCapacityUnavailable, host_capacity_for_run
from loregarden.services.stage_docker_capacity import (
    DockerCapacityUnavailable,
    docker_capacity_for_stage,
)
from sqlmodel import Session, select
from tests.factories import make_agent_run, make_ticket, make_workspace
from tests.test_capacity_host_pool import _reserve, _set_ceiling

STALL = 15.0


@pytest.fixture(name="session")
def session_fixture(isolated_db):
    with Session(isolated_db) as session:
        yield session


@pytest.fixture(name="claude", autouse=True)
def claude_fixture(monkeypatch):
    """The suite forces the in-process LOCAL adapter, which takes no host lease."""
    monkeypatch.setenv("LOREGARDEN_CLI_ADAPTER", CliAdapter.CLAUDE.value)


@pytest.fixture(name="workspace")
def workspace_fixture(session):
    return make_workspace(session, slug="waits-follow-the-line")


class ScriptedLine:
    """A fake clock whose sleeps end the leases scheduled to finish by then."""

    def __init__(self, session: Session) -> None:
        self.session = session
        self.now = 0.0
        self.finishes: list[tuple[float, str]] = []

    def finish_at(self, when: float, lease_id: str) -> None:
        self.finishes.append((when, lease_id))

    def clock(self) -> float:
        return self.now

    def sleep(self, _seconds: float) -> None:
        self.now += 1.0
        due = [lease for when, lease in self.finishes if when <= self.now]
        self.finishes = [(when, lease) for when, lease in self.finishes if when > self.now]
        for lease_id in due:
            docker_leases.release_lease(
                self.session, lease_id, reason=DockerLeaseEndReason.RUN_COMPLETED
            )

    def install(self, monkeypatch, module) -> None:
        """Make `module`'s waits run on this clock. A module that waits some other
        way never sees it, so its wait cannot be granted and the test fails."""
        monkeypatch.setattr(
            module,
            "wait_while_line_moves",
            partial(capacity_line.wait_while_line_moves, clock=self.clock, sleep=self.sleep),
        )


def _moving_line(session: Session, pool: CapacityPool, line: ScriptedLine) -> None:
    """Two holders filling a 4-cpu pool, and a 3-cpu waiter ahead of ours.

    The first holder finishing at t=10 is the line moving, though it frees too
    little for anyone: less than the waiter ahead needs, and less than a claim
    that may pass it. The second finishing at t=20 lets both start.
    """
    _set_ceiling(session, pool, cpus=4, memory_mb=8192, leases=6)
    first = _reserve(session, pool, label="short check", cpus=0.1)
    second = _reserve(session, pool, label="pre-push client-tests", cpus=3.9)
    ahead = _reserve(session, pool, label="gates land-verify", cpus=3)
    assert first.granted and second.granted and not ahead.granted
    line.finish_at(10, first.lease_id)
    line.finish_at(20, second.lease_id)


STALE = datetime(2026, 1, 1)


def _orchestration(session: Session, workspace) -> OrchestrationRun:
    """A running orchestration last seen long ago: a wait that does not renew its
    lease leaves it looking abandoned to the sweep while its driver waits."""
    ticket = make_ticket(session, workspace_id=workspace.id, state=TicketState.IN_PROGRESS)
    orch_run = OrchestrationRun(
        run_code=f"orch_{uuid4().hex[:6]}",
        ticket_id=ticket.id,
        workspace_id=workspace.id,
        status=OrchestrationRunStatus.RUNNING,
        last_seen_at=STALE,
    )
    session.add(orch_run)
    session.commit()
    return orch_run


def _renewed(session: Session, orch_run: OrchestrationRun) -> bool:
    session.expire_all()
    return session.get(OrchestrationRun, orch_run.id).last_seen_at > STALE


def _stalled_line(session: Session, pool: CapacityPool) -> None:
    _set_ceiling(session, pool, cpus=4, memory_mb=8192, leases=6)
    assert _reserve(session, pool, label="pre-push server-tests", cpus=4).granted


def test_an_agent_run_waits_out_a_moving_line_past_the_stall_window(
    session, workspace, monkeypatch
) -> None:
    line = ScriptedLine(session)
    line.install(monkeypatch, run_capacity)
    _moving_line(session, CapacityPool.HOST, line)
    orch_run = _orchestration(session, workspace)
    run = make_agent_run(
        session, workspace_id=workspace.id, stage_key="survey", orchestration_run_id=orch_run.id
    )

    with host_capacity_for_run(session, run, wait_seconds=STALL) as reservation:
        assert reservation is not None and reservation.granted
    assert line.now >= 20 > STALL
    assert _renewed(session, orch_run), "the waiting run's orchestration must stay alive"


def test_an_agent_run_gives_up_when_nothing_ahead_finishes_naming_the_holder(
    session, workspace, monkeypatch
) -> None:
    line = ScriptedLine(session)
    line.install(monkeypatch, run_capacity)
    _stalled_line(session, CapacityPool.HOST)
    run = make_agent_run(session, workspace_id=workspace.id, stage_key="survey")

    with pytest.raises(HostCapacityUnavailable, match="pre-push server-tests"):
        with host_capacity_for_run(session, run, wait_seconds=STALL):
            pytest.fail("the run started without its lease")


def test_a_stage_waits_out_a_moving_line_for_its_docker_lease(
    session, workspace, monkeypatch
) -> None:
    line = ScriptedLine(session)
    line.install(monkeypatch, stage_docker_capacity)
    _moving_line(session, CapacityPool.DOCKER, line)
    orch_run = _orchestration(session, workspace)
    run = make_agent_run(
        session, workspace_id=workspace.id, stage_key="e2e", orchestration_run_id=orch_run.id
    )
    stage = WorkflowStageDef(
        key="e2e",
        name="E2E",
        docker_footprint=DockerFootprint.CUSTOM,
        docker_cpus=1.0,
        docker_memory_mb=1024,
    )

    with docker_capacity_for_stage(session, run, stage, wait_seconds=STALL) as reservation:
        assert reservation is not None and reservation.granted
    assert line.now >= 20 > STALL
    assert _renewed(session, orch_run), "the waiting stage's orchestration must stay alive"


def test_a_stage_gives_up_when_nothing_ahead_finishes(session, workspace, monkeypatch) -> None:
    line = ScriptedLine(session)
    line.install(monkeypatch, stage_docker_capacity)
    _stalled_line(session, CapacityPool.DOCKER)
    run = make_agent_run(session, workspace_id=workspace.id, stage_key="e2e")
    stage = WorkflowStageDef(
        key="e2e",
        name="E2E",
        docker_footprint=DockerFootprint.CUSTOM,
        docker_cpus=1.0,
        docker_memory_mb=1024,
    )

    with pytest.raises(DockerCapacityUnavailable, match="pre-push server-tests"):
        with docker_capacity_for_stage(session, run, stage, wait_seconds=STALL):
            pytest.fail("the stage ran without its lease")


def test_an_orchestrations_parent_lease_waits_out_a_moving_line(
    session, workspace, monkeypatch
) -> None:
    line = ScriptedLine(session)
    line.install(monkeypatch, orchestration_capacity)
    _moving_line(session, CapacityPool.HOST, line)
    orch_run = _orchestration(session, workspace)

    reservation = _parent(session, orch_run, workspace)

    assert reservation.granted
    assert line.now >= 20 > STALL
    assert _renewed(session, orch_run), "the orchestration waiting for its parent stays alive"


def test_an_orchestrations_parent_wait_gives_up_its_place_when_the_line_stalls(
    session, workspace, monkeypatch
) -> None:
    line = ScriptedLine(session)
    line.install(monkeypatch, orchestration_capacity)
    _stalled_line(session, CapacityPool.HOST)
    orch_run = _orchestration(session, workspace)

    reservation = _parent(session, orch_run, workspace)

    assert not reservation.granted
    assert "pre-push server-tests" in reservation.message, "names who holds the line"
    waiting = session.exec(
        select(DockerLease).where(
            DockerLease.orchestration_run_id == orch_run.id,
            DockerLease.status == DockerLeaseStatus.WAITING,
        )
    ).all()
    assert waiting == [], "a waiter nobody polls would hold the head of the line"


def _parent(session: Session, orch_run: OrchestrationRun, workspace):
    return orchestration_capacity.acquire_or_find_parent(
        session,
        orch_run=orch_run,
        ticket=session.get(Ticket, orch_run.ticket_id),
        workspace=workspace,
        footprint=DockerFootprint.LIGHT,
        wait_seconds=STALL,
    )


def test_a_wait_that_stalls_on_its_first_poll_still_renews_its_orchestration(session) -> None:
    """A first poll slower than the whole stall budget (a loaded machine, a short
    gate budget) gave up before the heartbeat ever ran, so the orchestration that
    was faithfully waiting kept its stale lease: the merge-queue failure of
    `test_a_gate_waiting_in_line_keeps_its_orchestration_alive`."""
    _set_ceiling(session, CapacityPool.HOST, cpus=4, memory_mb=8192, leases=6)
    holder = _reserve(session, CapacityPool.HOST, label="pre-push server-tests", cpus=4)
    ours = _reserve(session, CapacityPool.HOST, label="gates survey_to_done", cpus=2)
    assert holder.granted and not ours.granted
    readings = iter([0.0])
    beats: list[str] = []

    stalled = capacity_line.wait_while_line_moves(
        session,
        ours,
        stall_seconds=0.3,
        on_poll=lambda: beats.append("renewed"),
        # Started at 0; every later reading is past the budget.
        clock=lambda: next(readings, 5.0),
        sleep=lambda _seconds: None,
    )

    assert not stalled.granted
    assert beats == ["renewed"]


def test_a_long_wait_writes_its_heartbeat_once_a_minute_not_every_poll(session) -> None:
    """The wait reads its place in line every poll, but stamping the lease and
    renewing the orchestration took SQLite's write lock each time: one per
    waiter every two seconds, exactly when the machine is busiest."""
    line = ScriptedLine(session)
    _set_ceiling(session, CapacityPool.HOST, cpus=4, memory_mb=8192, leases=6)
    holder = _reserve(session, CapacityPool.HOST, label="pre-push server-tests", cpus=4)
    ours = _reserve(session, CapacityPool.HOST, label="gates land-verify", cpus=2)
    assert holder.granted and not ours.granted
    line.finish_at(150, holder.lease_id)
    beats: list[float] = []

    granted = capacity_line.wait_while_line_moves(
        session,
        ours,
        stall_seconds=1000,
        on_poll=lambda: beats.append(line.now),
        clock=line.clock,
        sleep=line.sleep,
    )

    assert granted.granted
    assert beats == [0.0, 60.0, 120.0]


# --- A holder seen working is the line moving ------------------------------------
#
# On 2026-11-25 a push lost its place after 3h50m behind two suites that were both
# working, because only a lease *ending* restarted the clock. `capacity_run` reads
# the holders' activity now, and so do these waits: an agent run or a gate behind a
# pre-push suite that works past the window keeps its place.
#
# `WatchedLine` walks a fake clock in WATCH_STEP strides, and before the waiter reads
# the line each holder's sampler stamps its lease as of the fake wall clock, as
# `capacity_activity.CpuSync` does once a minute for a `capacity run` holder.

WATCH_STALL = 60.0
WATCH_STEP = 5.0

#: How a holder's sampler stamps its lease, given the fake wall clock.
Stamp = Callable[[DockerLease, datetime], None]


class WatchedLine:
    """A fake clock and wall clock, with each holder's sampler stamping as time passes."""

    def __init__(self, session: Session, monkeypatch) -> None:
        self.session = session
        self.began = utcnow()
        self.now = 0.0
        self._stamps: dict[str, Stamp] = {}
        self._ends: dict[str, float] = {}
        self._actions: list[tuple[float, Callable[[], None]]] = []
        monkeypatch.setattr(capacity_line, "utcnow", self.wall)

    def wall(self) -> datetime:
        return self.began + timedelta(seconds=self.now)

    def clock(self) -> float:
        return self.now

    def hold(
        self,
        label: str,
        stamp: Stamp,
        *,
        cpus: float,
        ends: float | None = None,
        pool: CapacityPool = CapacityPool.HOST,
        holder_kind: DockerHolderKind = DockerHolderKind.AD_HOC,
        takes_slot: bool = True,
    ) -> DockerLease:
        claim = docker_leases.reserve(
            self.session,
            holder_label=label,
            footprint=DockerFootprint.CUSTOM,
            cpus=cpus,
            memory_mb=512,
            pool=pool,
            holder_kind=holder_kind,
            takes_slot=takes_slot,
        )
        assert claim.granted, claim.message
        self._stamps[claim.lease_id] = stamp
        if ends is not None:
            self._ends[claim.lease_id] = ends
        self._sample()
        return self.session.get(DockerLease, claim.lease_id)

    def at(self, seconds: float, action: Callable[[], None]) -> None:
        """Do `action` on the first sleep that reaches `seconds`."""
        self._actions.append((seconds, action))

    def _sample(self) -> None:
        for lease_id, stamp in self._stamps.items():
            lease = self.session.get(DockerLease, lease_id)
            stamp(lease, self.wall())
            self.session.add(lease)
        self.session.commit()

    def sleep(self, _seconds: float) -> None:
        self.now += WATCH_STEP
        assert self.now < 24 * 3600, "still waiting"
        for lease_id in [held for held, ends in self._ends.items() if self.now >= ends]:
            del self._ends[lease_id], self._stamps[lease_id]
            docker_leases.release_lease(
                self.session, lease_id, reason=DockerLeaseEndReason.RUN_COMPLETED
            )
        due = [action for when, action in self._actions if when <= self.now]
        self._actions = [(when, action) for when, action in self._actions if when > self.now]
        for action in due:
            action()
        self._sample()

    def install(self, monkeypatch, module) -> None:
        monkeypatch.setattr(
            module,
            "wait_while_line_moves",
            partial(capacity_line.wait_while_line_moves, clock=self.clock, sleep=self.sleep),
        )

    def wait(self, waiter: DockerReservation):
        return capacity_line.wait_while_line_moves(
            self.session,
            waiter,
            stall_seconds=WATCH_STALL,
            clock=self.clock,
            sleep=self.sleep,
        )

    def assert_stalled_at(self, seconds: float) -> None:
        assert seconds <= self.now < seconds + 2 * WATCH_STEP


@pytest.fixture(name="watched")
def watched_fixture(session, monkeypatch) -> WatchedLine:
    _set_ceiling(session, CapacityPool.HOST, cpus=4, memory_mb=8192, leases=6)
    _set_ceiling(session, CapacityPool.DOCKER, cpus=4, memory_mb=8192, leases=6)
    return WatchedLine(session, monkeypatch)


def _unmeasured(_lease: DockerLease, _wall: datetime) -> None:
    """A holder no `capacity run` samples: an agent run, a gate, an older wrapper."""


def _busy(lease: DockerLease, wall: datetime) -> None:
    lease.cpu_sampled_at = lease.cpu_active_at = wall


def _hung(watched: WatchedLine) -> Stamp:
    """Sampled every pass, idle since 22 minutes before anyone queued behind it."""

    def stamp(lease: DockerLease, wall: datetime) -> None:
        lease.cpu_sampled_at = wall
        lease.cpu_active_at = watched.began - timedelta(minutes=22)

    return stamp


def _sampled_until(watched: WatchedLine, seconds: float) -> Stamp:
    """Works, and is watched working, until its sampler stops (a laptop asleep)."""

    def stamp(lease: DockerLease, wall: datetime) -> None:
        if wall <= watched.began + timedelta(seconds=seconds):
            _busy(lease, wall)

    return stamp


def _waiter(
    session: Session, label: str = "gates land-verify", cpus: float = 4
) -> DockerReservation:
    waiter = _reserve(session, CapacityPool.HOST, label=label, cpus=cpus)
    assert not waiter.granted
    return waiter


def test_an_agent_run_keeps_its_place_behind_a_suite_working_past_the_window(
    session, workspace, watched, monkeypatch
) -> None:
    """Nothing ahead ends for 150s against a 60s window; the suite is at work throughout."""
    watched.install(monkeypatch, run_capacity)
    watched.hold("pre-push server-tests", _busy, cpus=4, ends=150)
    run = make_agent_run(session, workspace_id=workspace.id, stage_key="survey")

    with host_capacity_for_run(session, run, wait_seconds=WATCH_STALL) as reservation:
        assert reservation is not None and reservation.granted
    assert watched.now >= 150 > 2 * WATCH_STALL


def test_a_hung_holder_still_stalls_the_line_after_the_window_and_is_named(
    session, watched
) -> None:
    watched.hold("deadlocked suite", _hung(watched), cpus=4)

    stalled = watched.wait(_waiter(session))

    assert not stalled.granted
    watched.assert_stalled_at(WATCH_STALL)
    assert "deadlocked suite (no CPU use or progress for" in stalled.message
    assert "may be hung" in stalled.message


def test_a_holder_nothing_measures_counts_as_neither_working_nor_idle(session, watched) -> None:
    """No reading: it does not keep the line alive, and it does not cut the window short."""
    watched.hold("agent run · stage implement", _unmeasured, cpus=4)

    stalled = watched.wait(_waiter(session))

    assert not stalled.granted
    watched.assert_stalled_at(WATCH_STALL)
    assert "may be hung" not in stalled.message


def test_one_busy_holder_is_enough_and_the_hung_one_beside_it_is_named(session, watched) -> None:
    watched.hold("deadlocked suite", _hung(watched), cpus=2)
    watched.hold("working suite", _busy, cpus=2, ends=150)

    stalled = watched.wait(_waiter(session))

    # Past the window while the other worked, then the hung one's own window.
    assert not stalled.granted
    watched.assert_stalled_at(150 + WATCH_STALL)
    assert "deadlocked suite (no CPU use" in stalled.message


def test_a_sampler_that_stopped_is_no_longer_a_reading(session, watched) -> None:
    """Its last word was "working"; once three samples are missed that is not known."""
    stopped_at = 20.0
    watched.hold("suite on a sleeping laptop", _sampled_until(watched, stopped_at), cpus=4)

    stalled = watched.wait(_waiter(session))

    assert not stalled.granted
    # A sample stands for three missed minutes before it says nothing.
    assert stopped_at + WATCH_STALL <= watched.now <= stopped_at + 180 + WATCH_STALL + WATCH_STEP


def test_a_holder_working_for_over_four_hours_no_longer_keeps_the_line_alive(
    session, watched
) -> None:
    """A loop spinning forever uses CPU forever; the wait is bounded by its hold's age."""
    spinning = watched.hold("infinite loop", _busy, cpus=4)
    spinning.granted_at = watched.began - timedelta(seconds=WORKING_HOLD_COUNTS_SECONDS)
    session.add(spinning)
    session.commit()

    stalled = watched.wait(_waiter(session))

    assert not stalled.granted
    watched.assert_stalled_at(WATCH_STALL)


def test_a_docker_stage_keeps_its_place_behind_a_host_suite_at_work(
    session, workspace, watched, monkeypatch
) -> None:
    """A docker claim is charged to the host pool too, so a host suite at work holds
    capacity the stage is waiting for: counting it is the rule, not a leak."""
    watched.install(monkeypatch, stage_docker_capacity)
    watched.hold("pre-push server-tests", _busy, cpus=4, ends=150)
    run = make_agent_run(session, workspace_id=workspace.id, stage_key="e2e")
    stage = WorkflowStageDef(
        key="e2e",
        name="E2E",
        docker_footprint=DockerFootprint.CUSTOM,
        docker_cpus=1.0,
        docker_memory_mb=1024,
    )

    with docker_capacity_for_stage(session, run, stage, wait_seconds=WATCH_STALL) as reservation:
        assert reservation is not None and reservation.granted
    assert watched.now >= 150


def test_a_docker_holder_is_unmeasured_whatever_its_stamps_say(session, watched) -> None:
    """Its work runs in the daemon's containers, so the wrapper's own CPU is not evidence."""
    watched.hold("compose stack", _busy, cpus=4, pool=CapacityPool.DOCKER)

    stalled = watched.wait(_waiter(session))

    assert not stalled.granted
    watched.assert_stalled_at(WATCH_STALL)


def _refused() -> DockerReservation:
    return DockerReservation(state=DockerGrantState.REJECTED, message="full")


def _retry_until(watched: WatchedLine, granted_at: float | None):
    """A gate child's retry: refused until `granted_at`, if ever."""
    return capacity_line.retry_while_pool_moves(
        watched.session,
        lambda: (
            DockerReservation(state=DockerGrantState.GRANTED, lease_id="child")
            if granted_at is not None and watched.now >= granted_at
            else _refused()
        ),
        retryable=lambda _reservation: True,
        waiting_for="gate child",
        stall_seconds=WATCH_STALL,
        clock=watched.clock,
        sleep=watched.sleep,
    )


def test_a_gate_child_keeps_asking_while_a_holder_works_past_the_window(watched) -> None:
    watched.hold("pre-push server-tests", _busy, cpus=4, ends=150)

    assert _retry_until(watched, granted_at=150).granted
    assert watched.now >= 150 > 2 * WATCH_STALL


def test_a_gate_child_gives_up_a_window_after_its_holder_hangs(watched) -> None:
    watched.hold("deadlocked suite", _hung(watched), cpus=4)

    refused = _retry_until(watched, granted_at=None)

    assert not refused.granted
    watched.assert_stalled_at(WATCH_STALL)
    assert "finished or was seen working" in refused.message


def test_a_standing_claim_taken_after_the_retry_began_does_not_keep_it_alive(watched) -> None:
    """It may pass the line, so neither its ending nor its work says the pool moves."""
    watched.hold("deadlocked suite", _hung(watched), cpus=2)
    watched.at(
        WATCH_STEP,
        lambda: watched.hold(
            "agent run · stage implement",
            _busy,
            cpus=0.25,
            holder_kind=DockerHolderKind.AGENT_RUN,
            takes_slot=False,
        ),
    )

    refused = _retry_until(watched, granted_at=None)

    assert not refused.granted
    watched.assert_stalled_at(WATCH_STALL)
