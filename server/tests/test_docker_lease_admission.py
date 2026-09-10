"""Admission: what fits, what waits, and what is refused outright.

The contract this file pins, beyond "the arithmetic adds up":

- **Both dimensions decide together.** A claim that fits on cpus and not on
  memory must not be granted. That is the bug a one-dimensional pool would have,
  and it is invisible until a machine is memory-bound.
- **Refused is not queued.** A claim larger than the whole ceiling can never be
  granted, so parking it would block everything behind it forever.
- **Strictly head-of-line.** A small waiter does not jump a large one. Fairness
  is the property here; backfill is deliberately not implemented.
- **A run holds one lease.** The `AgentSlot` double-claim (lg-workflow-integrity-568)
  in a new table, and the same reason release-by-run-id would be ambiguous.
"""

from __future__ import annotations

from unittest import mock

import pytest
from loregarden.models.domain import (
    DockerCeilingSource,
    DockerFootprint,
    DockerGrantState,
    DockerHolderKind,
    DockerLease,
    DockerLeaseEndReason,
    DockerLeaseStatus,
)
from loregarden.services import docker_leases
from loregarden.services.docker_capacity import Ceiling
from sqlmodel import Session, select
from tests.factories import make_agent_run, make_workspace


@pytest.fixture(name="session")
def session_fixture(isolated_db):
    with Session(isolated_db) as session:
        yield session


@pytest.fixture(name="ceiling")
def ceiling_fixture(session):
    """A ceiling of 4 cpus / 4096 MB / 3 leases, written straight to the pool.

    Set directly rather than through a fake `docker info`, so these tests are
    about admission and not about probing — `test_docker_capacity_service.py`
    owns the derivation.
    """
    pool = docker_leases.load_pool(session)
    pool.ceiling_cpus = 4.0
    pool.ceiling_memory_mb = 4096
    pool.ceiling_leases = 3
    pool.ceiling_source = DockerCeilingSource.CONFIG_OVERRIDE
    session.add(pool)
    session.commit()
    return pool


def _reserve(session, *, label="test", cpus=1.0, memory_mb=1024, **kwargs):
    return docker_leases.reserve(
        session,
        holder_label=label,
        footprint=DockerFootprint.CUSTOM,
        cpus=cpus,
        memory_mb=memory_mb,
        **kwargs,
    )


def test_a_claim_that_fits_is_granted(session, ceiling) -> None:
    reservation = _reserve(session, cpus=2.0, memory_mb=2048)
    assert reservation.state is DockerGrantState.GRANTED
    assert (reservation.cpus, reservation.memory_mb) == (2.0, 2048)
    assert reservation.expires_at is not None

    pool = docker_leases.load_pool(session)
    assert (pool.held_cpus, pool.held_memory_mb, pool.held_count) == (2.0, 2048, 1)


def test_a_claim_that_fits_on_cpus_but_not_memory_waits(session, ceiling) -> None:
    """The one-dimensional bug, pinned directly.

    Both dimensions are in one UPDATE's WHERE clause, so the database refuses
    this; a Python check that returned on the first satisfied dimension would
    grant it and over-book a memory-bound machine.
    """
    first = _reserve(session, cpus=1.0, memory_mb=3072)
    assert first.state is DockerGrantState.GRANTED

    second = _reserve(session, cpus=1.0, memory_mb=2048)
    assert second.state is DockerGrantState.QUEUED

    pool = docker_leases.load_pool(session)
    assert pool.held_memory_mb == 3072
    assert pool.held_memory_mb <= pool.ceiling_memory_mb


def test_the_lease_count_is_its_own_dimension(session, ceiling) -> None:
    """Three tiny claims fill a three-lease pool even with cpus to spare."""
    for index in range(3):
        assert _reserve(session, label=f"tiny-{index}", cpus=0.1, memory_mb=16).granted

    overflow = _reserve(session, label="tiny-4", cpus=0.1, memory_mb=16)
    assert overflow.state is DockerGrantState.QUEUED
    assert docker_leases.load_pool(session).held_count == 3


def test_a_claim_larger_than_the_whole_ceiling_is_refused_not_queued(session, ceiling) -> None:
    reservation = _reserve(session, cpus=99.0, memory_mb=99999)
    assert reservation.state is DockerGrantState.REJECTED
    assert reservation.error_kind == docker_leases.REJECT_EXCEEDS_CAPACITY
    assert not session.exec(select(DockerLease)).all(), "a refusal must not leave a waiter behind"


def test_an_unmeasured_machine_refuses_rather_than_admitting_freely(session) -> None:
    """Fail closed. No ceiling has ever been established and the probe fails, so
    there is no number to enforce — and admitting on no number is the failure
    this whole ledger exists to prevent."""
    with mock.patch.object(
        docker_leases,
        "refresh_ceiling",
        return_value=Ceiling(0.0, 0, 0, DockerCeilingSource.UNKNOWN, error="no daemon"),
    ):
        reservation = _reserve(session)
    assert reservation.state is DockerGrantState.REJECTED
    assert reservation.error_kind == docker_leases.REJECT_DOCKER_UNAVAILABLE
    assert "no daemon" in reservation.message


def test_release_promotes_the_waiter_behind_it(session, ceiling) -> None:
    held = _reserve(session, label="first", cpus=4.0, memory_mb=4096)
    queued = _reserve(session, label="second", cpus=2.0, memory_mb=2048)
    assert queued.state is DockerGrantState.QUEUED

    held.release()

    promoted = session.get(DockerLease, queued.lease_id)
    session.refresh(promoted)
    assert promoted.status is DockerLeaseStatus.HELD
    assert promoted.expires_at is not None
    assert docker_leases.load_pool(session).held_count == 1


def test_a_small_waiter_does_not_jump_a_large_one(session, ceiling) -> None:
    """Strict head-of-line, in the only case that can tell the difference.

    The ceiling is 4 cpus. A 2-cpu holder leaves room for the 0.5-cpu claim but
    not for the 3-cpu one queued ahead of it, so a drain that looked past a head
    it could not satisfy would grant the small one. It must not: skipping the
    head is how a stream of light claims starves a heavy one indefinitely.
    """
    holder = _reserve(session, label="holder", cpus=2.0, memory_mb=2048)
    big = _reserve(session, label="big", cpus=3.0, memory_mb=1024)
    small = _reserve(session, label="small", cpus=0.5, memory_mb=256)
    assert holder.granted
    assert big.state is DockerGrantState.QUEUED
    assert small.state is DockerGrantState.QUEUED, "the small claim fits, and must still wait"
    assert small.ahead == 1

    # And once the head can be satisfied, the drain keeps going rather than
    # stopping after one promotion.
    holder.release()
    assert session.get(DockerLease, big.lease_id).status is DockerLeaseStatus.HELD
    assert session.get(DockerLease, small.lease_id).status is DockerLeaseStatus.HELD


def test_a_run_that_already_holds_a_lease_gets_the_same_one_back(session, ceiling) -> None:
    """One run, one lease. Two would over-book the pool and make releasing by
    run id ambiguous — the AgentSlot double-claim in a new table.

    A real `AgentRun` row, not a made-up id: foreign keys are enforced on every
    engine this repo builds, so a bare string would fail at the INSERT and prove
    nothing about admission.
    """
    workspace = make_workspace(session, slug="docker-ws")
    run = make_agent_run(session, workspace_id=workspace.id)
    first = docker_leases.reserve(
        session,
        holder_label="stage",
        footprint=DockerFootprint.SERVICE,
        holder_kind=DockerHolderKind.AGENT_RUN,
        agent_run_id=run.id,
    )
    second = docker_leases.reserve(
        session,
        holder_label="stage again",
        footprint=DockerFootprint.STACK,
        holder_kind=DockerHolderKind.AGENT_RUN,
        agent_run_id=run.id,
    )
    assert first.granted and second.granted
    assert second.reused is True
    assert second.lease_id == first.lease_id
    assert docker_leases.load_pool(session).held_count == 1


def test_release_is_idempotent_and_does_not_double_credit_the_pool(session, ceiling) -> None:
    """A second release would decrement the pool twice and let admission run
    past its own ceiling — the reason `Reservation.release` guards too."""
    reservation = _reserve(session, cpus=2.0, memory_mb=2048)
    assert docker_leases.release_lease(session, reservation.lease_id) is True
    assert docker_leases.release_lease(session, reservation.lease_id) is False

    pool = docker_leases.load_pool(session)
    assert (pool.held_cpus, pool.held_memory_mb, pool.held_count) == (0.0, 0, 0)


def test_releasing_a_waiter_takes_it_out_of_the_line(session, ceiling) -> None:
    _reserve(session, label="holder", cpus=4.0, memory_mb=4096)
    waiter = _reserve(session, label="waiter", cpus=1.0, memory_mb=1024)
    assert waiter.state is DockerGrantState.QUEUED

    assert docker_leases.release_lease(
        session, waiter.lease_id, reason=DockerLeaseEndReason.ABANDONED
    )
    row = session.get(DockerLease, waiter.lease_id)
    assert row.status is DockerLeaseStatus.RELEASED
    assert row.end_reason is DockerLeaseEndReason.ABANDONED
    assert docker_leases.load_pool(session).held_count == 1


def test_renew_pushes_the_expiry_out(session, ceiling) -> None:
    reservation = _reserve(session)
    original = reservation.expires_at
    renewed = docker_leases.renew_lease(session, reservation.lease_id, ttl_seconds=1800)
    assert renewed is not None
    assert renewed > original


def test_renewing_a_settled_lease_reports_it_rather_than_pretending(session, ceiling) -> None:
    """None is distinguishable from a datetime, which is what the rule needs —
    the caller must be able to tell "renewed" from "there was nothing to renew"."""
    reservation = _reserve(session)
    reservation.release()
    assert docker_leases.renew_lease(session, reservation.lease_id) is None
    assert docker_leases.renew_lease(session, "no-such-lease") is None


def test_bind_records_what_the_holder_started(session, ceiling) -> None:
    """Capacity is reserved before `compose up`; the names only exist after. This
    is what upgrades the lease from clock-only reaping to probe-gated reaping."""
    reservation = _reserve(session)
    reservation.bind(compose_project="myapp", container_names=["myapp-db-1"])
    row = session.get(DockerLease, reservation.lease_id)
    session.refresh(row)
    assert row.compose_project == "myapp"
    assert "myapp-db-1" in row.container_names_json


def test_repair_recomputes_the_pool_from_the_ledger(session, ceiling) -> None:
    """The pool is a derived index; the ledger is authoritative. This is what
    bounds a crash between the pool update and the row commit to one sweep."""
    _reserve(session, cpus=1.0, memory_mb=1024)
    pool = docker_leases.load_pool(session)
    pool.held_cpus = 99.0
    pool.held_memory_mb = 99999
    pool.held_count = 42
    session.add(pool)
    session.commit()

    docker_leases.repair_pool(session)
    session.expire_all()
    pool = docker_leases.load_pool(session)
    assert (pool.held_cpus, pool.held_memory_mb, pool.held_count) == (1.0, 1024, 1)
