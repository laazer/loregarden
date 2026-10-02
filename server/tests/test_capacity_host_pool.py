"""The host pool, and the rule that nests the docker pool inside it.

- **A docker claim spends both pools; a host claim spends the host alone.**
  Containers run on the machine's cores, so a docker grant the host cannot
  afford is an over-booking whatever the Docker VM has free.
- **Booking two pools is one decision.** A claim the host refuses must leave
  the docker pool exactly as it was — no half-booking an observer could see,
  and none a crash could leak.
- **A host claim never needs docker.** Test suites must still queue on a
  machine whose Docker Desktop is stopped.
"""

from __future__ import annotations

import threading

import pytest
from loregarden.db.versions.capacity_host_pool import m_capacity_host_pool
from loregarden.models.domain import (
    CapacityPool,
    DockerCeilingSource,
    DockerFootprint,
    DockerGrantState,
    DockerLease,
    DockerLeaseStatus,
)
from loregarden.services import docker_leases
from loregarden.services.docker_leases import REJECT_DOCKER_UNAVAILABLE
from loregarden.services.docker_ledger import load_pool
from loregarden.services.docker_wait_estimate import HoldStats, estimate_waits
from sqlalchemy import text
from sqlmodel import Session, select


@pytest.fixture(name="session")
def session_fixture(isolated_db):
    with Session(isolated_db) as session:
        yield session


def _set_ceiling(session, pool: CapacityPool, *, cpus: float, memory_mb: int, leases: int = 8):
    row = load_pool(session, pool)
    row.ceiling_cpus = cpus
    row.ceiling_memory_mb = memory_mb
    row.ceiling_leases = leases
    row.ceiling_source = DockerCeilingSource.CONFIG_OVERRIDE
    session.add(row)
    session.commit()


def _reserve(session, pool: CapacityPool, *, label: str, cpus: float, memory_mb: int = 1024):
    return docker_leases.reserve(
        session,
        holder_label=label,
        footprint=DockerFootprint.CUSTOM,
        cpus=cpus,
        memory_mb=memory_mb,
        pool=pool,
    )


def _held(session, pool: CapacityPool) -> tuple[float, int, int]:
    session.expire_all()
    row = load_pool(session, pool)
    return (row.held_cpus, row.held_memory_mb, row.held_count)


def test_a_docker_claim_is_charged_to_both_pools_and_released_from_both(session) -> None:
    _set_ceiling(session, CapacityPool.DOCKER, cpus=4, memory_mb=8192)
    _set_ceiling(session, CapacityPool.HOST, cpus=8, memory_mb=16384)

    claim = _reserve(session, CapacityPool.DOCKER, label="stack", cpus=2, memory_mb=2048)
    assert claim.granted
    assert _held(session, CapacityPool.DOCKER) == (2, 2048, 1)
    assert _held(session, CapacityPool.HOST) == (2, 2048, 1)

    docker_leases.release_lease(session, claim.lease_id)
    assert _held(session, CapacityPool.DOCKER) == (0, 0, 0)
    assert _held(session, CapacityPool.HOST) == (0, 0, 0)


def test_a_host_claim_leaves_the_docker_pool_alone(session) -> None:
    _set_ceiling(session, CapacityPool.DOCKER, cpus=4, memory_mb=8192)
    _set_ceiling(session, CapacityPool.HOST, cpus=8, memory_mb=16384)

    claim = _reserve(session, CapacityPool.HOST, label="pytest", cpus=4, memory_mb=4096)

    assert claim.granted
    assert _held(session, CapacityPool.HOST) == (4, 4096, 1)
    assert _held(session, CapacityPool.DOCKER) == (0, 0, 0)


def test_a_host_claim_is_granted_while_docker_cannot_be_measured(session) -> None:
    """Docker Desktop stopped must not stop test suites from queueing."""

    def unreachable(_argv):
        raise FileNotFoundError("docker: command not found")

    _set_ceiling(session, CapacityPool.HOST, cpus=8, memory_mb=16384)

    host = docker_leases.reserve(
        session,
        holder_label="pytest",
        footprint=DockerFootprint.HEAVY,
        pool=CapacityPool.HOST,
        invoke=unreachable,
    )
    docker = docker_leases.reserve(
        session,
        holder_label="stack",
        footprint=DockerFootprint.LIGHT,
        pool=CapacityPool.DOCKER,
        invoke=unreachable,
    )

    assert host.granted
    assert docker.state is DockerGrantState.REJECTED
    assert docker.error_kind == REJECT_DOCKER_UNAVAILABLE


def test_a_docker_claim_the_host_cannot_afford_waits_and_books_nothing(session) -> None:
    """The atomicity property: the docker pool has room, the host does not, and
    the docker pool must come out of the failed booking untouched."""
    _set_ceiling(session, CapacityPool.DOCKER, cpus=4, memory_mb=8192)
    _set_ceiling(session, CapacityPool.HOST, cpus=4, memory_mb=8192)
    assert _reserve(session, CapacityPool.HOST, label="pytest", cpus=3.5).granted

    claim = _reserve(session, CapacityPool.DOCKER, label="stack", cpus=1)

    assert claim.state is DockerGrantState.QUEUED
    assert _held(session, CapacityPool.DOCKER) == (0, 0, 0)
    assert _held(session, CapacityPool.HOST) == (3.5, 1024, 1)


def test_a_claim_bigger_than_the_host_is_refused_naming_the_host(session) -> None:
    _set_ceiling(session, CapacityPool.DOCKER, cpus=8, memory_mb=16384)
    _set_ceiling(session, CapacityPool.HOST, cpus=4, memory_mb=16384)

    claim = _reserve(session, CapacityPool.DOCKER, label="stack", cpus=6)

    assert claim.state is DockerGrantState.REJECTED
    assert claim.error_kind == docker_leases.REJECT_EXCEEDS_CAPACITY
    assert "host" in claim.message


def test_releasing_a_host_holder_starts_the_docker_waiter_it_was_blocking(session) -> None:
    _set_ceiling(session, CapacityPool.DOCKER, cpus=4, memory_mb=8192)
    _set_ceiling(session, CapacityPool.HOST, cpus=4, memory_mb=8192)
    holder = _reserve(session, CapacityPool.HOST, label="pytest", cpus=4)
    waiter = _reserve(session, CapacityPool.DOCKER, label="stack", cpus=2)
    assert waiter.state is DockerGrantState.QUEUED

    docker_leases.release_lease(session, holder.lease_id)

    session.expire_all()
    assert session.get(DockerLease, waiter.lease_id).status is DockerLeaseStatus.HELD
    assert _held(session, CapacityPool.DOCKER) == (2, 1024, 1)
    assert _held(session, CapacityPool.HOST) == (2, 1024, 1)


def test_repair_counts_docker_claims_in_the_host_and_host_claims_only_there(session) -> None:
    _set_ceiling(session, CapacityPool.DOCKER, cpus=8, memory_mb=16384)
    _set_ceiling(session, CapacityPool.HOST, cpus=16, memory_mb=32768)
    _reserve(session, CapacityPool.DOCKER, label="stack", cpus=2, memory_mb=2048)
    _reserve(session, CapacityPool.HOST, label="pytest", cpus=3, memory_mb=3072)
    # Corrupt both totals; the ledger rows are the truth.
    for pool in CapacityPool:
        row = load_pool(session, pool)
        row.held_cpus, row.held_memory_mb, row.held_count = 99, 99, 99
        session.add(row)
    session.commit()

    docker_leases.repair_pool(session)

    assert _held(session, CapacityPool.DOCKER) == (2, 2048, 1)
    assert _held(session, CapacityPool.HOST) == (5, 5120, 2)


def test_concurrent_mixed_claims_never_overbook_either_pool(isolated_db) -> None:
    """Eight threads, docker and host claims interleaved, against pools sized so
    the host is the binding one. Afterwards each pool's total equals the sum of
    the leases charged to it, and neither exceeds its ceiling."""
    with Session(isolated_db) as session:
        _set_ceiling(session, CapacityPool.DOCKER, cpus=4, memory_mb=8192, leases=8)
        _set_ceiling(session, CapacityPool.HOST, cpus=5, memory_mb=8192, leases=8)

    barrier = threading.Barrier(8)
    errors: list[BaseException] = []

    def claim(index: int) -> None:
        pool = CapacityPool.DOCKER if index % 2 else CapacityPool.HOST
        try:
            with Session(isolated_db) as session:
                barrier.wait()
                _reserve(session, pool, label=f"claim-{index}", cpus=1, memory_mb=512)
        except BaseException as exc:  # noqa: BLE001 — collected and asserted below
            errors.append(exc)

    threads = [threading.Thread(target=claim, args=(i,)) for i in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert errors == []

    with Session(isolated_db) as session:
        held = list(
            session.exec(select(DockerLease).where(DockerLease.status == DockerLeaseStatus.HELD))
        )
        docker_held = [lease for lease in held if lease.pool is CapacityPool.DOCKER]
        assert _held(session, CapacityPool.HOST) == (len(held), 512 * len(held), len(held))
        assert _held(session, CapacityPool.DOCKER) == (
            len(docker_held),
            512 * len(docker_held),
            len(docker_held),
        )
        assert len(held) == 5  # the host's 5 cpus, whatever the arrival order
        assert len(docker_held) <= 4


def test_a_host_waiter_behind_a_docker_holder_is_estimated_from_that_holder(session) -> None:
    _set_ceiling(session, CapacityPool.DOCKER, cpus=4, memory_mb=8192)
    _set_ceiling(session, CapacityPool.HOST, cpus=4, memory_mb=8192)
    assert _reserve(session, CapacityPool.DOCKER, label="stack", cpus=4).granted
    waiter = _reserve(session, CapacityPool.HOST, label="pytest", cpus=2)
    stats = HoldStats(
        by_footprint={(CapacityPool.DOCKER, DockerFootprint.CUSTOM): 120.0},
        overall=120.0,
        samples=3,
    )

    estimate = estimate_waits(session, stats=stats)[waiter.lease_id]

    assert estimate.seconds == pytest.approx(120, abs=2)


def test_the_migration_seeds_the_host_pool_with_live_docker_claims(isolated_db) -> None:
    """A database upgraded with leases held must not start the host at zero."""
    with Session(isolated_db) as session:
        _set_ceiling(session, CapacityPool.DOCKER, cpus=8, memory_mb=16384)
        _set_ceiling(session, CapacityPool.HOST, cpus=16, memory_mb=32768)
        _reserve(session, CapacityPool.DOCKER, label="stack", cpus=2, memory_mb=2048)

    with isolated_db.begin() as conn:
        conn.execute(text("DELETE FROM docker_capacity_pool WHERE id = 'host'"))
        m_capacity_host_pool(conn)
        m_capacity_host_pool(conn)  # re-running is a no-op

    with Session(isolated_db) as session:
        assert _held(session, CapacityPool.HOST) == (2, 2048, 1)
