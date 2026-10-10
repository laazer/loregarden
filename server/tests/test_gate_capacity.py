"""A transition's gate commands hold host capacity while they run.

Acceptance criteria (lg-machine-resource-858), as amended with the operator:

1. Heavy gate commands run under a host lease. A top-level one, not a child of
   the run's lease: gates run in `_advance_after_stage`, after the run's own
   lease is released, so the orchestrator holds nothing while it waits.
2. A gate that cannot get capacity within its budget fails visibly
   (GateRunResult ok=False, UNAVAILABLE, naming capacity) and never silently
   skips — the command does not run, and the result is not a pass.
"""

from __future__ import annotations

import pytest
from loregarden.models.domain import (
    CapacityPool,
    DockerFootprint,
    DockerLease,
    DockerLeaseEndReason,
    DockerLeaseStatus,
    GateOutcome,
)
from loregarden.services.docker_ledger import load_pool
from loregarden.services.gate_runner import run_transition_gates
from loregarden.services.orchestration_profile import GatesConfig, OrchestrationProfile
from sqlmodel import Session, select
from tests.factories import make_ticket, make_workspace
from tests.test_capacity_host_pool import _reserve, _set_ceiling


@pytest.fixture(name="session")
def session_fixture(isolated_db):
    with Session(isolated_db) as session:
        yield session


@pytest.fixture(name="host")
def host_fixture(session):
    _set_ceiling(session, CapacityPool.HOST, cpus=8, memory_mb=16384, leases=4)


@pytest.fixture(name="workspace")
def workspace_fixture(session, tmp_path):
    workspace = make_workspace(session, slug="gate-capacity")
    workspace.repo_path = str(tmp_path)
    session.add(workspace)
    session.commit()
    return workspace


@pytest.fixture(name="ticket")
def ticket_fixture(session, workspace):
    return make_ticket(session, workspace_id=workspace.id)


def _profile(commands: list[str], footprint=DockerFootprint.HEAVY) -> OrchestrationProfile:
    return OrchestrationProfile(
        slug="gate-capacity",
        gates=GatesConfig(enabled=True, commands=commands, capacity_footprint=footprint),
    )


def _gate(session, profile, workspace, ticket, **kwargs):
    return run_transition_gates(
        session, profile, workspace, ticket, from_stage="implement", to_stage="verify", **kwargs
    )


def _gate_leases(session) -> list[DockerLease]:
    session.expire_all()
    return [
        lease
        for lease in session.exec(select(DockerLease))
        if lease.holder_label.startswith("gates ")
    ]


def test_gate_commands_run_under_a_host_lease_sized_by_the_profile(
    session, host, workspace, ticket, tmp_path
) -> None:
    seen = tmp_path / "seen"
    profile = _profile(
        [f'sh -c "echo $LOREGARDEN_CAPACITY_WORKERS $LOREGARDEN_CAPACITY_LEASE_ID > {seen}"']
    )

    result = _gate(session, profile, workspace, ticket)

    assert result.ok and result.outcome is GateOutcome.PASSED
    (lease,) = _gate_leases(session)
    workers, lease_id = seen.read_text().split()
    assert (workers, lease_id) == ("4", lease.id)  # heavy = 4 cpus, while the lease held
    assert (lease.cpus, lease.memory_mb, lease.pool) == (4.0, 8192, CapacityPool.HOST)
    assert "implement_to_verify" in lease.holder_label
    assert lease.status is DockerLeaseStatus.RELEASED
    assert load_pool(session, CapacityPool.HOST).held_cpus == 0


def test_the_lease_is_released_when_a_gate_fails(session, host, workspace, ticket) -> None:
    result = _gate(session, _profile(["false"]), workspace, ticket)

    assert result.outcome is GateOutcome.FAILED
    (lease,) = _gate_leases(session)
    assert lease.status is DockerLeaseStatus.RELEASED


def test_a_gate_that_cannot_get_capacity_is_unavailable_and_runs_nothing(
    session, host, workspace, ticket, tmp_path, monkeypatch
) -> None:
    monkeypatch.setattr("loregarden.config.settings.gate_capacity_wait_seconds", 0.3)
    _reserve(session, CapacityPool.HOST, label="pre-push", cpus=8)
    ran = tmp_path / "ran"

    result = _gate(session, _profile([f"touch {ran}"]), workspace, ticket)

    assert not result.ok
    assert result.outcome is GateOutcome.UNAVAILABLE, "a busy machine is not a gate failure"
    assert "host capacity" in result.message
    assert not ran.exists(), "the gate ran without its capacity"
    (lease,) = _gate_leases(session)
    assert lease.end_reason is DockerLeaseEndReason.ABANDONED


def test_footprint_none_runs_unreserved(session, host, workspace, ticket) -> None:
    result = _gate(session, _profile(["true"], DockerFootprint.NONE), workspace, ticket)

    assert result.outcome is GateOutcome.PASSED
    assert _gate_leases(session) == []


def test_a_transition_with_nothing_to_run_takes_no_lease(session, host, workspace, ticket) -> None:
    result = _gate(session, _profile(["", "  "]), workspace, ticket)

    assert result.outcome is GateOutcome.SKIPPED
    assert _gate_leases(session) == []


def test_the_profile_yaml_spelling_round_trips() -> None:
    """Profiles load from agent_context/orchestration/*.yaml as plain strings."""
    assert GatesConfig.model_validate({"capacity_footprint": "stack"}).capacity_footprint is (
        DockerFootprint.STACK
    )
    assert GatesConfig().capacity_footprint is DockerFootprint.SERVICE
