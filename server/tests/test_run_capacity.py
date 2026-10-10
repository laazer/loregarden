"""An agent run holds a small host lease for exactly as long as it executes.

Acceptance criteria (lg-machine-resource-859):

1. Run dispatch reserves before spawning and releases on every terminal path,
   including a crash and a reap.
2. Lease sizes come from measured runs, and the calibration is recorded with a
   date (`run_capacity.RUNTIME_WEIGHTS`).

Decided with the operator: an agent's own pushes queue normally, so a run's
lease takes no lease-count slot; a run waits up to
`agent_run_capacity_wait_seconds` for its lease, then fails naming the wait.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest import mock

import pytest
from loregarden.config import settings
from loregarden.models.domain import (
    CapacityPool,
    CliAdapter,
    DockerFootprint,
    DockerGrantState,
    DockerLease,
    DockerLeaseEndReason,
    DockerLeaseStatus,
    RunStatus,
    WorkflowStageDef,
)
from loregarden.services import docker_leases, docker_reaper, run_capacity
from loregarden.services.docker_leases import docker_capacity_for_stage
from loregarden.services.docker_ledger import load_pool
from loregarden.services.run_capacity import (
    RUNTIME_WEIGHTS,
    HostCapacityUnavailable,
    host_capacity_for_run,
    runtime_for_run,
)
from loregarden.services.run_service import RunService
from sqlmodel import Session, select
from tests.factories import make_agent_run, make_ticket, make_workspace
from tests.test_capacity_host_pool import _reserve, _set_ceiling


@pytest.fixture(name="session")
def session_fixture(isolated_db):
    with Session(isolated_db) as session:
        yield session


@pytest.fixture(name="claude", autouse=True)
def claude_fixture(monkeypatch):
    """The suite forces the in-process LOCAL adapter, which holds nothing."""
    monkeypatch.setenv("LOREGARDEN_CLI_ADAPTER", CliAdapter.CLAUDE.value)


@pytest.fixture(name="pools")
def pools_fixture(session):
    _set_ceiling(session, CapacityPool.HOST, cpus=4, memory_mb=8192, leases=1)
    _set_ceiling(session, CapacityPool.DOCKER, cpus=4, memory_mb=8192, leases=2)


@pytest.fixture(name="run")
def run_fixture(session):
    workspace = make_workspace(session, slug="run-capacity")
    return make_agent_run(session, workspace_id=workspace.id, stage_key="implement")


def _run_leases(session, run) -> list[DockerLease]:
    session.expire_all()
    return list(session.exec(select(DockerLease).where(DockerLease.agent_run_id == run.id)))


def test_a_claude_run_holds_its_measured_size_for_the_block_and_no_slot(
    session, pools, run
) -> None:
    with host_capacity_for_run(session, run) as reservation:
        assert reservation is not None and reservation.granted
        (lease,) = _run_leases(session, run)
        assert (lease.cpus, lease.memory_mb) == RUNTIME_WEIGHTS[CliAdapter.CLAUDE]
        assert lease.pool is CapacityPool.HOST and lease.takes_slot is False
        assert load_pool(session, CapacityPool.HOST).held_count == 0

    (lease,) = _run_leases(session, run)
    assert lease.status is DockerLeaseStatus.RELEASED
    assert lease.end_reason is DockerLeaseEndReason.RUN_COMPLETED
    assert load_pool(session, CapacityPool.HOST).held_cpus == 0


def test_the_lease_is_released_when_the_run_crashes(session, pools, run) -> None:
    with pytest.raises(RuntimeError, match="agent blew up"):
        with host_capacity_for_run(session, run):
            raise RuntimeError("agent blew up")

    (lease,) = _run_leases(session, run)
    assert lease.status is DockerLeaseStatus.RELEASED


def test_a_local_run_takes_nothing(session, pools, run, monkeypatch) -> None:
    monkeypatch.setenv("LOREGARDEN_CLI_ADAPTER", CliAdapter.LOCAL.value)

    with host_capacity_for_run(session, run) as reservation:
        assert reservation is None

    assert _run_leases(session, run) == []


def test_an_agents_push_still_gets_the_only_slot(session, pools, run) -> None:
    """The deadlock the operator ruled out by sizing: with one slot, a run that
    took it would leave its own push waiting behind itself forever."""
    with host_capacity_for_run(session, run):
        push = _reserve(session, CapacityPool.HOST, label="pre-push", cpus=2)

        assert push.granted


def test_a_full_machine_makes_the_run_wait_then_fail_naming_the_wait(session, pools, run) -> None:
    _reserve(session, CapacityPool.HOST, label="pre-push", cpus=4)

    with pytest.raises(HostCapacityUnavailable, match="host capacity: still queued"):
        with host_capacity_for_run(session, run, wait_seconds=0.3):
            pytest.fail("the run started without its lease")

    (lease,) = _run_leases(session, run)
    assert lease.status is DockerLeaseStatus.RELEASED
    assert lease.end_reason is DockerLeaseEndReason.ABANDONED


def test_a_waiting_run_keeps_its_place_in_line(session, pools, run) -> None:
    """A run waits up to 30 minutes; the abandonment sweep drops a waiter after
    10 unless it is seen polling, so each pass must count as a poll."""
    _reserve(session, CapacityPool.HOST, label="pre-push", cpus=4)

    with pytest.raises(HostCapacityUnavailable):
        with host_capacity_for_run(session, run, wait_seconds=0.3):
            pass

    (lease,) = _run_leases(session, run)
    assert lease.poll_count > 0 and lease.last_polled_at is not None


def test_a_docker_stage_run_nests_its_host_lease_and_never_waits(session, pools, run) -> None:
    stage = WorkflowStageDef(key="e2e", name="E2E", docker_footprint=DockerFootprint.STACK)
    _set_ceiling(session, CapacityPool.HOST, cpus=4, memory_mb=8192, leases=2)
    # Pre-push 2 cpus + the stack's 2: the host's cpus are then exactly full,
    # so a top-level host claim for the run would queue.
    _reserve(session, CapacityPool.HOST, label="pre-push", cpus=2)

    with docker_capacity_for_stage(session, run, stage) as docker:
        assert docker is not None and docker.granted
        with host_capacity_for_run(session, run, docker_parent=docker, wait_seconds=0):
            host = next(
                lease for lease in _run_leases(session, run) if lease.pool is CapacityPool.HOST
            )
            assert host.parent_lease_id == docker.lease_id
            assert host.covered_cpus == RUNTIME_WEIGHTS[CliAdapter.CLAUDE][0]


def test_the_reaper_keeps_a_live_runs_lease_and_reclaims_a_dead_ones(session, pools, run) -> None:
    later = datetime.now(timezone.utc) + timedelta(hours=1)
    with host_capacity_for_run(session, run):
        lease_id = _run_leases(session, run)[0].id
        with mock.patch.object(docker_reaper, "_run_is_live", return_value=True):
            report = docker_reaper.reap_docker_leases(session, now=later)
        assert lease_id in report.renewed

        run.status = RunStatus.FAILED
        session.add(run)
        session.commit()
        docker_reaper.reap_docker_leases(session, now=later + timedelta(hours=1))
        reaped = session.get(DockerLease, lease_id)
        session.refresh(reaped)
        assert reaped.status is DockerLeaseStatus.RELEASED
        assert reaped.end_reason is DockerLeaseEndReason.TTL_EXPIRED


def test_the_runtime_is_resolved_the_way_the_executor_resolves_it(
    session, run, monkeypatch
) -> None:
    monkeypatch.delenv("LOREGARDEN_CLI_ADAPTER")
    with mock.patch.object(run_capacity, "get_agent", return_value={"adapter": "lmstudio"}):
        assert runtime_for_run(session, run) is CliAdapter.LMSTUDIO


def test_the_ledger_switches_turn_it_off(session, pools, run, monkeypatch) -> None:
    monkeypatch.setattr(settings, "agent_run_capacity_enabled", False)

    with host_capacity_for_run(session, run) as reservation:
        assert reservation is None


def test_dispatch_holds_the_lease_while_the_agent_executes(session, pools) -> None:
    """The real bracket in RunService, with the agent itself stubbed out."""
    workspace = make_workspace(session, slug="run-capacity-dispatch")
    ticket = make_ticket(session, workspace_id=workspace.id)
    run = make_agent_run(session, workspace_id=workspace.id, ticket_id=ticket.id)
    service = RunService(session)
    seen: list[DockerGrantState | DockerLeaseStatus] = []

    def execute(executing_run, _ticket):
        seen.extend(lease.status for lease in _run_leases(session, executing_run))
        return executing_run

    with (
        mock.patch.object(service.orchestration, "start_run", return_value=run),
        mock.patch.object(service.executor, "execute", side_effect=execute),
    ):
        service.start_and_execute(ticket)

    assert seen == [DockerLeaseStatus.HELD]
    assert [lease.status for lease in _run_leases(session, run)] == [DockerLeaseStatus.RELEASED]
    assert docker_leases.load_pool(session, CapacityPool.HOST).held_cpus == 0
