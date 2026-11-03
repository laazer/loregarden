"""A transition's gate commands hold host capacity while they run.

Acceptance criteria (lg-machine-resource-858), as amended with the operator:

1. Heavy gate commands run under a host lease. A top-level one, not a child of
   the run's lease: gates run in `_advance_after_stage`, after the run's own
   lease is released, so the orchestrator holds nothing while it waits.
2. A gate that cannot get capacity within its budget fails visibly
   (GateRunResult ok=False, UNAVAILABLE, naming capacity) and never silently
   skips — the command does not run, and the result is not a pass.

Amended by lg-machine-resource-1923: a gate handed its run's parent lease claims
a CHILD of it instead, and a child never queues. 858's reasoning still holds for
a gate with no parent — a land verification, a standalone transition — and those
tests are unchanged. The two at the bottom cover the child branch.
"""

from __future__ import annotations

import pytest
from loregarden.models.domain import (
    CapacityPool,
    DockerFootprint,
    DockerGrantState,
    DockerHolderKind,
    DockerLease,
    DockerLeaseEndReason,
    DockerLeaseStatus,
    GateOutcome,
)
from loregarden.services.docker_ledger import load_pool
from loregarden.services.gate_capacity import GateCapacityUnavailable, acquire_gate_capacity
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


# ---- lg-machine-resource-1923 R2: the gate's claim is a child of the run's parent ----
#
# A gate child is priced from the parent's unlent remainder, any excess is
# booked against the pools ahead of the queue, or it is refused at once. It
# never calls `wait_for_grant`: the thread asking is the thread holding the
# parent, and a holder that waits is the shape `capacity_children` forbids.


def _parent(session, ticket, workspace, *, cpus: float = 0.5):
    """The run-scoped parent, shaped as `orchestration_capacity` creates it."""
    from loregarden.services.docker_leases import reserve

    reservation = reserve(
        session,
        holder_label=f"run orch_abc123 · {workspace.slug} · {ticket.external_id}",
        footprint=DockerFootprint.CUSTOM,
        cpus=cpus,
        memory_mb=512,
        pool=CapacityPool.HOST,
        holder_kind=DockerHolderKind.ORCHESTRATION,
        ticket_id=ticket.id,
        workspace_id=workspace.id,
        takes_slot=False,
    )
    assert reservation.granted
    return reservation


def _child_lease(session, ticket, workspace, parent, footprint, transition):
    return acquire_gate_capacity(
        session,
        footprint,
        ticket=ticket,
        workspace=workspace,
        transition=transition,
        ttl_seconds=600,
        parent_lease_id=parent.lease_id,
    )


def test_a_gate_child_books_the_excess_ahead_of_the_queue(
    session, host, workspace, ticket, monkeypatch
) -> None:
    """R2. The intended mode, not a mis-sizing.

    A parent that fully covers its 1.0-cpu children is unaffordable on a 7-cpu
    machine at six concurrent tickets, so a LIGHT parent books 0.5 cpu of excess
    on every transition. What makes that safe is that it is bounded and stated:
    at most 0.5 standing plus 0.5 excess per in-flight ticket.

    The pool here is 8 cpu: 0.5 parent, a 5.0 holder, and a 4.0 waiter that
    cannot fit — the 1912 arrangement. The child is granted anyway, and the
    waiter keeps its place.
    """

    def never_waits(*args, **kwargs):  # pragma: no cover - asserted, never called
        raise AssertionError("a gate child queued; a holder that waits can deadlock")

    monkeypatch.setattr("loregarden.services.gate_capacity.wait_for_grant", never_waits)
    parent = _parent(session, ticket, workspace)
    _reserve(session, CapacityPool.HOST, label="agent runs", cpus=5.0)
    waiter = _reserve(session, CapacityPool.HOST, label="land-verify lg-other-1", cpus=4.0)
    assert waiter.state is DockerGrantState.QUEUED

    lease = _child_lease(
        session, ticket, workspace, parent, DockerFootprint.SERVICE, "plan_to_plan-synthesis"
    )

    assert lease.lease_id
    row = session.get(DockerLease, lease.lease_id)
    assert row.parent_lease_id == parent.lease_id
    assert row.status is DockerLeaseStatus.HELD
    assert (row.cpus, row.covered_cpus) == (1.0, 0.5), "0.5 from the parent, 0.5 from the pools"
    assert row.granted_at == row.requested_at, "a child never spends time in the line"
    assert row.takes_slot is False
    # The line is left exactly as it was: the excess was booked, not the head's turn.
    session.expire_all()
    assert session.get(DockerLease, waiter.lease_id).status is DockerLeaseStatus.WAITING
    assert load_pool(session, CapacityPool.HOST).held_cpus == 6.0  # 0.5 + 5.0 + 0.5 excess
    lease.release(session)
    assert load_pool(session, CapacityPool.HOST).held_cpus == 5.5, (
        "releasing the child returns its excess and its cover, never the parent's grant"
    )


def test_a_gate_child_that_exceeds_the_parent_grant_is_refused_not_queued(
    session, host, workspace, ticket
) -> None:
    """R2. Refused at once, with the four facts an operator needs to act on.

    `exceeds_parent_grant`, never QUEUED. And the message is the same two
    numbers the capacity board shows — the ask, and what the parent has left to
    lend — so an operator reading the ticket and an operator reading the board
    are looking at one event rather than two.
    """
    parent = _parent(session, ticket, workspace)
    _reserve(session, CapacityPool.HOST, label="pre-push server-tests", cpus=7.5)
    first = _child_lease(
        session, ticket, workspace, parent, DockerFootprint.LIGHT, "triage_to_plan"
    )
    assert first.lease_id, "a child inside the parent's grant is granted with the pool full"

    with pytest.raises(GateCapacityUnavailable) as refusal:
        _child_lease(
            session, ticket, workspace, parent, DockerFootprint.SERVICE, "plan_to_plan-synthesis"
        )

    message = str(refusal.value)
    assert "exceeds_parent_grant" in message
    assert parent.lease_id in message, "the parent's lease id, so the board reads beside it"
    assert "0.5" in message, "the parent's grant in cpu"
    assert "0 cpus" in message, f"the unlent remainder, which is what 'needs more' means: {message}"
    assert "triage_to_plan" in message, "each live OCCUPYING child, by its holder label"
    # A refusal books nothing and leaves nobody in the line.
    session.expire_all()
    assert load_pool(session, CapacityPool.HOST).held_cpus == 8.0  # 7.5 + the 0.5 parent
    assert [
        lease
        for lease in session.exec(select(DockerLease))
        if lease.status is DockerLeaseStatus.WAITING and lease.holder_label.startswith("gates ")
    ] == []
    first.release(session)
