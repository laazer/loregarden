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

from datetime import datetime
from functools import partial
from uuid import uuid4

import pytest
from loregarden.models.domain import (
    CapacityPool,
    CliAdapter,
    DockerFootprint,
    DockerLease,
    DockerLeaseEndReason,
    DockerLeaseStatus,
    OrchestrationRun,
    OrchestrationRunStatus,
    Ticket,
    TicketState,
    WorkflowStageDef,
)
from loregarden.services import (
    capacity_line,
    docker_leases,
    orchestration_capacity,
    run_capacity,
    stage_docker_capacity,
)
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
    """Two 2-cpu holders on a 4-cpu pool, and a 3-cpu waiter ahead of ours.

    The first holder finishing at t=10 is the line moving, though it frees too
    little for the waiter ahead. The second finishing at t=20 lets both start.
    """
    _set_ceiling(session, pool, cpus=4, memory_mb=8192, leases=6)
    first = _reserve(session, pool, label="pre-push server-tests", cpus=2)
    second = _reserve(session, pool, label="pre-push client-tests", cpus=2)
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
