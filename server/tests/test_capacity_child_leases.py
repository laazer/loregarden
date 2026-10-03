"""Child leases: a holder asking for more must not wait behind anyone, itself included.

Acceptance criteria (lg-machine-resource-857):

1. A child within its parent's grant is granted immediately, even with waiters queued.
2. Random nested claims never deadlock and never exceed either pool's ceiling.
3. Releasing a parent releases its children.
"""

from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone

import pytest
from loregarden.models.domain import (
    CapacityPool,
    DockerFootprint,
    DockerGrantState,
    DockerLease,
    DockerLeaseEndReason,
    DockerLeaseStatus,
)
from loregarden.services import docker_leases, docker_reaper
from loregarden.services.capacity_children import reserve_child
from loregarden.services.capacity_run import CapacityRequest, acquire
from loregarden.services.docker_leases import (
    REJECT_EXCEEDS_PARENT_GRANT,
    REJECT_PARENT_NOT_HELD,
    REJECT_PARENT_POOL_MISMATCH,
)
from loregarden.services.docker_ledger import CHARGED_POOLS, OCCUPYING, booked, load_pool
from sqlmodel import Session, select
from tests.test_capacity_host_pool import _reserve, _set_ceiling


@pytest.fixture(name="session")
def session_fixture(isolated_db):
    with Session(isolated_db) as session:
        yield session


@pytest.fixture(name="host")
def host_fixture(session):
    _set_ceiling(session, CapacityPool.HOST, cpus=8, memory_mb=8192)
    _set_ceiling(session, CapacityPool.DOCKER, cpus=8, memory_mb=8192)


def _top(session, label: str, cpus: float, memory_mb: int = 1024, pool=CapacityPool.HOST):
    return _reserve(session, pool, label=label, cpus=cpus, memory_mb=memory_mb)


def _child(session, parent_id: str, label: str, cpus: float, memory_mb: int = 512, pool=None):
    return reserve_child(
        session,
        parent_lease_id=parent_id,
        holder_label=label,
        footprint=DockerFootprint.CUSTOM,
        cpus=cpus,
        memory_mb=memory_mb,
        pool=pool,
    )


def _host_held(session) -> tuple[float, int, int]:
    session.expire_all()
    row = load_pool(session, CapacityPool.HOST)
    return (row.held_cpus, row.held_memory_mb, row.held_count)


def _lease(session, lease_id: str) -> DockerLease:
    session.expire_all()
    lease = session.get(DockerLease, lease_id)
    assert lease is not None
    return lease


def test_a_child_inside_its_parents_grant_jumps_a_full_queue(session, host) -> None:
    parent = _top(session, "agent", cpus=8, memory_mb=4096)
    waiter = _top(session, "next push", cpus=2)
    assert waiter.state is DockerGrantState.QUEUED

    child = _child(session, parent.lease_id, "gate", cpus=3, memory_mb=1024)

    assert child.granted
    assert _host_held(session) == (8, 4096, 1)  # nothing new booked
    assert _lease(session, waiter.lease_id).status is DockerLeaseStatus.WAITING
    assert _lease(session, parent.lease_id).child_covered_cpus == 3


def test_a_childs_excess_is_booked_from_the_pools_without_a_slot(session, host) -> None:
    parent = _top(session, "agent", cpus=4, memory_mb=1024)

    child = _child(session, parent.lease_id, "gate", cpus=6, memory_mb=1024)

    assert child.granted
    lease = _lease(session, child.lease_id)
    assert (lease.covered_cpus, lease.covered_memory_mb) == (4, 1024)
    assert _host_held(session) == (6, 1024, 1)


def test_an_excess_that_does_not_fit_is_refused_at_once_and_books_nothing(session, host) -> None:
    parent = _top(session, "agent", cpus=4, memory_mb=1024)
    _top(session, "other agent", cpus=4, memory_mb=1024)
    first = _child(session, parent.lease_id, "gate-1", cpus=2)
    before = (_host_held(session), _lease(session, parent.lease_id).child_covered_cpus)

    second = _child(session, parent.lease_id, "gate-2", cpus=3)

    assert first.granted
    assert second.state is DockerGrantState.REJECTED
    assert second.error_kind == REJECT_EXCEEDS_PARENT_GRANT
    assert (_host_held(session), _lease(session, parent.lease_id).child_covered_cpus) == before


def test_the_canonical_deadlock_resolves_at_once(session, host) -> None:
    """Two agents fill the machine; each one's gate needs more than its agent
    holds. Queued, each gate would wait for room only the other agent's finish
    frees, and neither agent finishes until its gate runs. Both are refused
    immediately instead, and the machine stays exactly as full as it was."""
    first = _top(session, "agent-a", cpus=4)
    second = _top(session, "agent-b", cpus=4)

    gates = [
        _child(session, first.lease_id, "gate-a", cpus=6),
        _child(session, second.lease_id, "gate-b", cpus=6),
    ]

    assert [gate.error_kind for gate in gates] == [REJECT_EXCEEDS_PARENT_GRANT] * 2
    assert [gate.state for gate in gates] == [DockerGrantState.REJECTED] * 2
    assert _host_held(session) == (8, 2048, 2)


def test_releasing_a_child_returns_only_what_it_booked(session, host) -> None:
    parent = _top(session, "agent", cpus=4, memory_mb=1024)
    child = _child(session, parent.lease_id, "gate", cpus=6, memory_mb=1024)

    docker_leases.release_lease(session, child.lease_id)

    assert _host_held(session) == (4, 1024, 1)
    assert _lease(session, parent.lease_id).child_covered_cpus == 0
    assert _lease(session, parent.lease_id).status is DockerLeaseStatus.HELD


def test_releasing_a_parent_releases_its_children_and_starts_the_line(session, host) -> None:
    parent = _top(session, "agent", cpus=6, memory_mb=1024)
    child = _child(session, parent.lease_id, "gate", cpus=7, memory_mb=1024)
    grandchild = _child(session, child.lease_id, "nested", cpus=1, memory_mb=256)
    waiter = _top(session, "next push", cpus=4)
    assert waiter.state is DockerGrantState.QUEUED

    docker_leases.release_lease(session, parent.lease_id)

    for lease_id in (child.lease_id, grandchild.lease_id):
        lease = _lease(session, lease_id)
        assert lease.status is DockerLeaseStatus.RELEASED
        assert lease.end_reason is DockerLeaseEndReason.PARENT_RELEASED
    assert _lease(session, waiter.lease_id).status is DockerLeaseStatus.HELD
    assert _host_held(session) == (4, 1024, 1)


def test_a_docker_child_cannot_draw_on_a_host_parent(session, host) -> None:
    parent = _top(session, "agent", cpus=4)

    child = _child(session, parent.lease_id, "stack", cpus=1, pool=CapacityPool.DOCKER)

    assert child.error_kind == REJECT_PARENT_POOL_MISMATCH


def test_a_host_child_can_draw_on_a_docker_parent(session, host) -> None:
    parent = _top(session, "stack", cpus=4, pool=CapacityPool.DOCKER)

    child = _child(session, parent.lease_id, "pytest", cpus=2, pool=CapacityPool.HOST)

    assert child.granted
    assert _lease(session, child.lease_id).covered_cpus == 2


def test_a_child_of_an_ended_parent_is_refused(session, host) -> None:
    parent = _top(session, "agent", cpus=4)
    docker_leases.release_lease(session, parent.lease_id)

    child = _child(session, parent.lease_id, "gate", cpus=1)

    assert child.error_kind == REJECT_PARENT_NOT_HELD


def test_a_child_lives_while_its_parent_does_and_ends_with_it(session, host) -> None:
    parent = _top(session, "agent", cpus=4)
    child = _child(session, parent.lease_id, "gate", cpus=1)
    later = datetime.now(timezone.utc) + timedelta(hours=1)
    # The parent renews itself; only the child's clock has run out.
    docker_leases.renew_lease(session, parent.lease_id, ttl_seconds=7200)

    report = docker_reaper.reap_docker_leases(session, now=later)

    assert child.lease_id in report.renewed
    assert _lease(session, child.lease_id).status is DockerLeaseStatus.HELD

    parent_row = _lease(session, parent.lease_id)
    parent_row.expires_at = later - timedelta(seconds=1)
    session.add(parent_row)
    session.commit()
    docker_reaper.reap_docker_leases(session, now=later)

    assert _lease(session, parent.lease_id).status is DockerLeaseStatus.RELEASED
    assert _lease(session, child.lease_id).end_reason is DockerLeaseEndReason.PARENT_RELEASED


def test_repair_counts_only_what_children_booked(session, host) -> None:
    parent = _top(session, "agent", cpus=4, memory_mb=1024)
    child = _child(session, parent.lease_id, "gate", cpus=6, memory_mb=1536)
    for pool in CapacityPool:
        row = load_pool(session, pool)
        row.held_cpus, row.held_memory_mb, row.held_count = 99, 99, 99
        session.add(row)
    parent_row = _lease(session, parent.lease_id)
    parent_row.child_covered_cpus = 99
    session.add(parent_row)
    session.commit()

    docker_leases.repair_pool(session)

    assert _host_held(session) == (6, 1536, 1)
    assert _lease(session, parent.lease_id).child_covered_cpus == 4
    assert _lease(session, child.lease_id).covered_cpus == 4


def test_capacity_run_nests_under_a_parent_instead_of_queueing(session, host) -> None:
    parent = _top(session, "pre-push", cpus=8)
    assert _top(session, "next push", cpus=2).state is DockerGrantState.QUEUED

    reservation = acquire(
        session,
        CapacityRequest(label="nested", cpus=2, memory_mb=512, parent_lease_id=parent.lease_id),
        report=lambda _line: None,
    )

    assert reservation.granted
    assert _lease(session, reservation.lease_id).parent_lease_id == parent.lease_id


# ---- the property ------------------------------------------------------


def _check_invariants(session) -> None:
    session.expire_all()
    leases = list(session.exec(select(DockerLease)))
    occupying = [lease for lease in leases if lease.status in OCCUPYING]
    for pool in CapacityPool:
        row = load_pool(session, pool)
        charged = [lease for lease in occupying if pool in CHARGED_POOLS[lease.pool]]
        cpus = sum(booked(lease).cpus for lease in charged)
        memory = sum(booked(lease).memory_mb for lease in charged)
        count = sum(booked(lease).count for lease in charged)
        assert row.held_cpus == pytest.approx(cpus), pool
        assert (row.held_memory_mb, row.held_count) == (memory, count), pool
        assert row.held_cpus <= row.ceiling_cpus + 1e-9, pool
        assert row.held_memory_mb <= row.ceiling_memory_mb, pool
        assert row.held_count <= row.ceiling_leases, pool
    for parent in occupying:
        children = [lease for lease in occupying if lease.parent_lease_id == parent.id]
        drawn = sum(lease.covered_cpus for lease in children)
        assert parent.child_covered_cpus == pytest.approx(drawn)
        assert drawn <= parent.cpus + 1e-9
    for lease in leases:
        if lease.parent_lease_id and lease.status in OCCUPYING:
            parent = session.get(DockerLease, lease.parent_lease_id)
            assert parent is not None and parent.status in OCCUPYING, "orphaned child"


def _occupying(session) -> list[DockerLease]:
    session.expire_all()
    return list(session.exec(select(DockerLease).where(DockerLease.status.in_(OCCUPYING))))


def _waiting(session) -> list[DockerLease]:
    session.expire_all()
    return list(
        session.exec(select(DockerLease).where(DockerLease.status == DockerLeaseStatus.WAITING))
    )


@pytest.mark.parametrize("seed", range(12))
def test_random_nested_claims_never_deadlock_or_overbook(session, seed) -> None:
    """Never asserts how children avoid deadlock — only that none happens.

    Children may only be released by their parent, and a parent with a waiting
    descendant cannot finish; any design that lets a child wait behind capacity
    its own ancestry holds will stall the liveness phase below."""
    rng = random.Random(seed)
    _set_ceiling(session, CapacityPool.HOST, cpus=8, memory_mb=8192, leases=5)
    _set_ceiling(session, CapacityPool.DOCKER, cpus=5, memory_mb=6144, leases=3)
    pools = list(CapacityPool)

    for step in range(60):
        roll = rng.random()
        holders = _occupying(session)
        if roll < 0.35 or not holders:
            _top(
                session,
                f"top-{step}",
                cpus=rng.choice([0.5, 1, 2, 3, 4]),
                memory_mb=rng.choice([256, 1024, 2048]),
                pool=rng.choice(pools),
            )
        elif roll < 0.7:
            parent = rng.choice(holders)
            _child(
                session,
                parent.id,
                f"child-{step}",
                cpus=rng.choice([0.5, 1, 2, 3]),
                memory_mb=rng.choice([128, 512, 1024]),
                pool=rng.choice([None, *pools]),
            )
        else:
            docker_leases.release_lease(session, rng.choice(holders).id)
        _check_invariants(session)

    # Liveness, modelled honestly: a holder can finish only once none of its
    # children is still waiting — a parent blocked on its own child is exactly
    # the deadlock. Release finishable holders in any order; the line must empty.
    for _ in range(200):
        waiting = _waiting(session)
        blocked = {lease.parent_lease_id for lease in waiting if lease.parent_lease_id}
        holders = [
            lease
            for lease in _occupying(session)
            if not lease.parent_lease_id and not _blocked(session, lease.id, blocked)
        ]
        if not _occupying(session) and not waiting:
            break
        assert holders, "every holder waits on its own child: deadlock"
        docker_leases.release_lease(session, rng.choice(holders).id)
        _check_invariants(session)
    else:
        pytest.fail("the line never emptied")


def _blocked(session, lease_id: str, blocked: set[str]) -> bool:
    """Whether this lease, or any lease nested under it, has a child in the line."""
    if lease_id in blocked:
        return True
    children = session.exec(
        select(DockerLease.id).where(
            DockerLease.parent_lease_id == lease_id, DockerLease.status.in_(OCCUPYING)
        )
    ).all()
    return any(_blocked(session, child, blocked) for child in children)
