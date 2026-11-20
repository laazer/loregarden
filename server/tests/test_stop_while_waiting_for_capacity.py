"""Pause stops a run wherever it is: in the capacity line, or mid-run.

On 2026-11-19 Pause on lg-code-knowledge-1765 did nothing. Its triage run was
committed RUNNING and sat in the host-capacity line behind heavy pre-push
suites with no agent process yet. The stop set `cancel_requested_at`, found no
pid to signal, and returned. The line wait polled only for a grant or a stall
(up to an hour), so the run kept waiting and the board kept showing it running.

And a stop that did reach a running agent blocked the ticket "Cancelled by
operator": the orchestrator treated a CANCELLED run as a failed one.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import timedelta
from functools import partial
from unittest import mock

import pytest
from loregarden.models.domain import (
    AgentRun,
    CapacityPool,
    CliAdapter,
    DockerFootprint,
    DockerLease,
    DockerLeaseStatus,
    OrchestrationRun,
    OrchestrationRunStatus,
    RunStatus,
    StageStatus,
    TicketState,
    WorkflowStageDef,
)
from loregarden.models.domain.enums import utcnow
from loregarden.services import builtin_orchestrator, capacity_line, run_capacity
from loregarden.services import stage_docker_capacity as stage_docker
from loregarden.services.builtin_orchestrator import BuiltinOrchestrator
from loregarden.services.capacity_errors import CapacityWaitStopped
from loregarden.services.orchestration import OrchestrationService
from loregarden.services.run_cancellation import (
    request_cancel,
    request_orchestration_cancel,
)
from loregarden.services.run_capacity import host_capacity_for_run
from loregarden.services.run_lease import RunStoppedBeforeStart, supervised
from loregarden.services.stage_docker_capacity import docker_capacity_for_stage
from loregarden.services.stopped_run_invariant import (
    STOP_HONOURED_WITHIN,
    settle_stopped_runs,
    stopped_runs_still_in_flight,
)
from sqlmodel import Session, select
from tests.factories import make_agent_run, make_workspace
from tests.test_auto_mode_subtree import _make_ticket, _make_workspace, _profile, _stage_status
from tests.test_capacity_host_pool import _reserve, _set_ceiling

STALL = 1000.0
STOP_AT = 3.0


@pytest.fixture(name="session")
def session_fixture(isolated_db):
    with Session(isolated_db) as session:
        yield session


@pytest.fixture(name="claude", autouse=True)
def claude_fixture(monkeypatch):
    """The suite forces the in-process LOCAL adapter, which takes no host lease."""
    monkeypatch.setenv("LOREGARDEN_CLI_ADAPTER", CliAdapter.CLAUDE.value)


class StopInLine:
    """A fake clock on a line that never moves; the operator stops the run at t=3."""

    def __init__(self, session: Session, run: AgentRun) -> None:
        self.session = session
        self.run = run
        self.now = 0.0

    def clock(self) -> float:
        return self.now

    def sleep(self, _seconds: float) -> None:
        self.now += 1.0
        if self.now == STOP_AT:
            request_cancel(self.session, self.session.get(AgentRun, self.run.id))

    def install(self, monkeypatch, module) -> None:
        monkeypatch.setattr(
            module,
            "wait_while_line_moves",
            partial(capacity_line.wait_while_line_moves, clock=self.clock, sleep=self.sleep),
        )


def _waiting_leases(session: Session, run: AgentRun) -> list[DockerLease]:
    session.expire_all()
    return list(
        session.exec(
            select(DockerLease).where(
                DockerLease.agent_run_id == run.id,
                DockerLease.status == DockerLeaseStatus.WAITING,
            )
        ).all()
    )


def _full(session: Session, pool: CapacityPool) -> None:
    _set_ceiling(session, pool, cpus=4, memory_mb=8192, leases=6)
    assert _reserve(session, pool, label="pre-push server-tests", cpus=4).granted


def test_a_run_stopped_in_the_host_line_leaves_it_at_once(session, monkeypatch) -> None:
    workspace = make_workspace(session, slug="stop-in-host-line")
    run = make_agent_run(
        session, workspace_id=workspace.id, stage_key="triage", status=RunStatus.RUNNING
    )
    line = StopInLine(session, run)
    line.install(monkeypatch, run_capacity)
    _full(session, CapacityPool.HOST)

    with pytest.raises(CapacityWaitStopped):
        with host_capacity_for_run(session, run, wait_seconds=STALL):
            pytest.fail("a stopped run was granted its lease")

    assert line.now == STOP_AT, "the wait must end on the poll that sees the stop"
    assert _waiting_leases(session, run) == [], "its place in line must be given back"


def test_a_run_stopped_in_the_docker_line_leaves_it_at_once(session, monkeypatch) -> None:
    workspace = make_workspace(session, slug="stop-in-docker-line")
    run = make_agent_run(
        session, workspace_id=workspace.id, stage_key="e2e", status=RunStatus.RUNNING
    )
    line = StopInLine(session, run)
    line.install(monkeypatch, stage_docker)
    _full(session, CapacityPool.DOCKER)
    stage = WorkflowStageDef(
        key="e2e",
        name="E2E",
        docker_footprint=DockerFootprint.CUSTOM,
        docker_cpus=1.0,
        docker_memory_mb=1024,
    )

    with pytest.raises(CapacityWaitStopped):
        with docker_capacity_for_stage(session, run, stage, wait_seconds=STALL):
            pytest.fail("a stopped stage was granted its lease")

    assert line.now == STOP_AT
    assert _waiting_leases(session, run) == []


def test_supervised_turns_a_stopped_wait_into_a_stop_before_start(session) -> None:
    workspace = make_workspace(session, slug="stop-supervised")
    run = make_agent_run(session, workspace_id=workspace.id, status=RunStatus.RUNNING)

    @contextmanager
    def stopped_in_line() -> Iterator[None]:
        raise CapacityWaitStopped("stopped in line")
        yield  # pragma: no cover - the generator shape a context manager needs

    with pytest.raises(RunStoppedBeforeStart):
        with supervised(run.id, stopped_in_line()):
            pytest.fail("the agent started on a stopped run")


def test_a_stop_landing_with_the_grant_never_reaches_the_body(session) -> None:
    """The race the line's own check cannot see: granted on the same poll the stop
    arrived. The agent's cancel poll would spawn it only to kill it."""
    workspace = make_workspace(session, slug="stop-with-grant")
    run = make_agent_run(session, workspace_id=workspace.id, status=RunStatus.RUNNING)
    exited = []

    @contextmanager
    def granted_as_the_stop_lands() -> Iterator[None]:
        request_cancel(session, run)
        try:
            yield
        finally:
            exited.append(True)

    with pytest.raises(RunStoppedBeforeStart):
        with supervised(run.id, granted_as_the_stop_lands()):
            pytest.fail("the agent started on a stopped run")

    assert exited == [True], "the capacity granted to a stopped run was not handed back"


@pytest.fixture(name="ticket")
def ticket_fixture(db_session: Session, tmp_path):
    workspace = _make_workspace(db_session, tmp_path, "pause-stops")
    return _make_ticket(db_session, workspace, external_id="pause-stops-1", title="Pause me")


def _pause_like_the_board(session: Session, ticket) -> None:
    """What `POST /tickets/{id}/stop` does: flag every in-flight run, then the orchestration."""
    session.expire_all()
    for run in session.exec(
        select(AgentRun).where(
            AgentRun.ticket_id == ticket.id, AgentRun.status == RunStatus.RUNNING
        )
    ).all():
        request_cancel(session, run)
    for orch_run in session.exec(
        select(OrchestrationRun).where(
            OrchestrationRun.ticket_id == ticket.id,
            OrchestrationRun.status == OrchestrationRunStatus.RUNNING,
        )
    ).all():
        request_orchestration_cancel(session, orch_run)


def _assert_paused_not_blocked(session: Session, ticket, orch_run: OrchestrationRun) -> None:
    session.expire_all()
    session.refresh(ticket)
    run = session.exec(select(AgentRun).where(AgentRun.ticket_id == ticket.id)).one()
    assert run.status == RunStatus.CANCELLED
    assert orch_run.status == OrchestrationRunStatus.CANCELLED
    assert ticket.state != TicketState.BLOCKED
    assert ticket.blocking_issues == ""
    assert _stage_status(session, ticket, "work") == StageStatus.PENDING


def test_pause_while_waiting_in_line_cancels_without_starting_the_agent(
    db_session: Session, ticket
) -> None:
    @contextmanager
    def stopped_in_line(session, _run) -> Iterator[None]:
        _pause_like_the_board(session, ticket)
        raise CapacityWaitStopped("stopped in line")
        yield  # pragma: no cover - the generator shape a context manager needs

    orchestrator = BuiltinOrchestrator(db_session)
    with (
        mock.patch.object(builtin_orchestrator, "run_capacity", stopped_in_line),
        mock.patch.object(orchestrator.executor, "execute") as execute,
    ):
        orch_run = orchestrator.execute(ticket, _profile(), max_stages=1)

    execute.assert_not_called()
    _assert_paused_not_blocked(db_session, ticket, orch_run)


def test_pause_mid_run_cancels_the_orchestration_instead_of_blocking(
    db_session: Session, ticket
) -> None:
    orchestrator = BuiltinOrchestrator(db_session)

    def agent_stopped_mid_run(run: AgentRun, _ticket) -> AgentRun:
        _pause_like_the_board(db_session, ticket)
        db_session.refresh(run)
        return OrchestrationService(db_session).complete_run(
            run, status=RunStatus.CANCELLED, stderr="Cancelled by operator"
        )

    with mock.patch.object(orchestrator.executor, "execute", side_effect=agent_stopped_mid_run):
        orch_run = orchestrator.execute(ticket, _profile(), max_stages=1)

    _assert_paused_not_blocked(db_session, ticket, orch_run)


def test_a_run_whose_stop_nobody_honoured_is_found_and_settled(session) -> None:
    """The state Pause left behind before the fix: in flight, flagged, nothing acting."""
    workspace = make_workspace(session, slug="stop-unhonoured")
    stale = make_agent_run(
        session,
        workspace_id=workspace.id,
        run_code="run_stale_stop",
        status=RunStatus.RUNNING,
        cancel_requested_at=utcnow() - STOP_HONOURED_WITHIN - timedelta(minutes=1),
    )
    stopping = make_agent_run(
        session,
        workspace_id=workspace.id,
        run_code="run_still_stopping",
        status=RunStatus.RUNNING,
        cancel_requested_at=utcnow(),
    )

    violations = stopped_runs_still_in_flight(session)

    assert [v.subject_id for v in violations] == [stale.id], "a run still stopping is not stuck"
    assert settle_stopped_runs(session, violations) == 1
    session.expire_all()
    assert session.get(AgentRun, stale.id).status == RunStatus.CANCELLED
    assert session.get(AgentRun, stopping.id).status == RunStatus.RUNNING
    assert stopped_runs_still_in_flight(session) == []
