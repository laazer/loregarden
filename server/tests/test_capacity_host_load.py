"""The host pool grants against what the machine has idle, not only its size.

On 2026-11-09 a suite granted "heavy" (4 of the host ceiling's 7 cpus) ran at load
average 41-72 on 10 cores: the window server, a browser and the dev server's
reloader used about 7 cores the ledger never saw. The properties:

- busy cores outside the ledger are measured beside the ceiling, capped at the
  core count, without counting the ledger's own holders twice;
- a failed load read counts the machine as busy, never as idle (fail closed);
- that load holds back a claim of a core or more while the ledger holds
  anything, and is ignored for small claims, for an empty ledger (make's `-l`
  rule) and after a claim's patience runs out, so it cannot starve the line;
- the board's shortfall names the same free figure the drain judged by.
"""

from __future__ import annotations

from datetime import timedelta
from unittest import mock

import pytest
from loregarden.config import settings
from loregarden.models.domain import (
    CapacityPool,
    CapacityResource,
    DockerCeilingSource,
    DockerFootprint,
    DockerLease,
    DockerLeaseStatus,
)
from loregarden.models.domain.enums import utcnow
from loregarden.services import docker_leases, host_capacity
from loregarden.services.docker_capacity import Ceiling
from loregarden.services.docker_ledger import load_pool, shortfalls
from sqlmodel import Session

_PROBED = Ceiling(cpus=7.0, memory_mb=48000, leases=6, source=DockerCeilingSource.PROBE)


def _load(value: float):
    return lambda: value


def _fails() -> float:
    raise OSError(22, "getloadavg failed")


# ---- measuring --------------------------------------------------------------


@pytest.mark.parametrize(
    ("load", "held", "external"),
    [
        (9.0, 4.0, 5.0),  # 9 busy, 4 of them the ledger's own
        (60.0, 4.0, 6.0),  # a load of 60 on 10 cores is "full", not 56 more cores
        (2.0, 4.0, 0.0),  # holders not yet in the 1-minute average
        (3.5, 0.0, 3.5),
    ],
)
def test_outside_load_is_busy_cores_less_what_the_ledger_holds(load, held, external) -> None:
    measured = host_capacity.external_cpus(
        ncpu=10, ceiling_cpus=7.0, held_cpus=held, read_load=_load(load)
    )

    assert (measured.cpus, measured.error) == (external, "")


@pytest.mark.parametrize(("reader", "cause"), [(_fails, "getloadavg failed"), (_load(-1), "-1")])
def test_an_unreadable_load_counts_the_machine_as_busy(reader, cause) -> None:
    measured = host_capacity.external_cpus(
        ncpu=10, ceiling_cpus=7.0, held_cpus=1.0, read_load=reader
    )

    assert measured.cpus == 7.0
    assert cause in measured.error


@pytest.mark.parametrize(
    "ceiling",
    [
        Ceiling(cpus=7.0, memory_mb=1, leases=1, source=DockerCeilingSource.CONFIG_OVERRIDE),
        Ceiling(cpus=0.0, memory_mb=0, leases=0, source=DockerCeilingSource.UNKNOWN),
    ],
)
def test_no_load_is_recorded_for_an_override_or_an_unknown_ceiling(ceiling) -> None:
    assert host_capacity.host_external_load(ceiling, held_cpus=0.0, read_load=_fails).cpus == 0.0


def test_switching_the_gate_off_records_no_load(monkeypatch) -> None:
    monkeypatch.setattr(settings, "host_load_aware", False)

    assert host_capacity.host_external_load(_PROBED, held_cpus=0.0, read_load=_load(9)).cpus == 0


# ---- recording --------------------------------------------------------------


@pytest.fixture(name="session")
def session_fixture(isolated_db):
    with Session(isolated_db) as session:
        yield session


@pytest.fixture(name="probed")
def probed_fixture(monkeypatch) -> None:
    """The host ceiling comes from the probe (conftest overrides it), on a 10-core box."""
    monkeypatch.setattr(settings, "host_capacity_cpus", 0.0)
    monkeypatch.setattr(settings, "host_capacity_memory_mb", 0)
    monkeypatch.setattr(host_capacity.os, "cpu_count", lambda: 10)


def test_a_host_measurement_records_the_outside_load(session, probed) -> None:
    with mock.patch.object(host_capacity.os, "getloadavg", return_value=(9.5, 0.0, 0.0)):
        docker_leases.refresh_ceiling(session, pool_name=CapacityPool.HOST)

    row = load_pool(session, CapacityPool.HOST)
    assert row.ceiling_source is DockerCeilingSource.PROBE
    assert row.external_cpus == 9.5
    assert row.probe_error == ""


def test_a_failed_load_read_is_recorded_as_busy_and_says_why(session, probed) -> None:
    with mock.patch.object(host_capacity.os, "getloadavg", side_effect=OSError(5, "nope")):
        docker_leases.refresh_ceiling(session, pool_name=CapacityPool.HOST)

    row = load_pool(session, CapacityPool.HOST)
    assert row.external_cpus == row.ceiling_cpus > 0
    assert "could not read the load average" in row.probe_error


def test_a_waiter_is_judged_by_a_fresh_load_reading(session, probed) -> None:
    with mock.patch.object(host_capacity.os, "getloadavg", return_value=(10.0, 0.0, 0.0)):
        docker_leases.refresh_ceiling(session, pool_name=CapacityPool.HOST)
        holder = _reserve(session, label="suite", cpus=1.0)
        waiter = _reserve(session, label="second suite", cpus=2.0)
    assert holder.granted and not waiter.granted

    row = load_pool(session, CapacityPool.HOST)
    row.probed_at = utcnow() - timedelta(seconds=settings.host_load_max_age_seconds + 1)
    session.add(row)
    session.commit()
    with mock.patch.object(host_capacity.os, "getloadavg", return_value=(1.0, 0.0, 0.0)):
        promoted = docker_leases.drain_waiters(session)

    assert promoted == [waiter.lease_id]


# ---- granting ---------------------------------------------------------------


def _host(session, *, cpus: float = 7.0, external: float = 0.0) -> None:
    """A host pool of `cpus` with `external` busy outside the ledger.

    Marked as an override so the drain does not re-measure this machine's real
    load over the figure the test set.
    """
    row = load_pool(session, CapacityPool.HOST)
    row.ceiling_cpus = cpus
    row.ceiling_memory_mb = 64 * 1024
    row.ceiling_leases = 8
    row.ceiling_source = DockerCeilingSource.CONFIG_OVERRIDE
    row.external_cpus = external
    session.add(row)
    session.commit()


def _set_external(session, external: float) -> None:
    session.expire_all()
    row = load_pool(session, CapacityPool.HOST)
    row.external_cpus = external
    session.add(row)
    session.commit()


def _reserve(session, *, label: str, cpus: float):
    return docker_leases.reserve(
        session,
        holder_label=label,
        footprint=DockerFootprint.CUSTOM,
        cpus=cpus,
        memory_mb=512,
        pool=CapacityPool.HOST,
    )


def test_an_empty_ledger_admits_its_head_whatever_the_load(session) -> None:
    _host(session, external=7.0)

    assert _reserve(session, label="suite", cpus=4.0).granted


def test_outside_load_holds_back_a_claim_beside_a_holder(session) -> None:
    _host(session, external=6.0)
    assert _reserve(session, label="stack", cpus=1.0).granted

    waiter = _reserve(session, label="suite", cpus=4.0)

    assert not waiter.granted
    lease = session.get(DockerLease, waiter.lease_id)
    (short,) = shortfalls(CapacityPool.HOST, load_pool(session, CapacityPool.HOST), lease)
    assert (short.resource, short.free) == (CapacityResource.CPUS, 0.0)


def test_the_held_back_claim_is_granted_when_the_load_goes(session) -> None:
    _host(session, external=6.0)
    assert _reserve(session, label="stack", cpus=1.0).granted
    waiter = _reserve(session, label="suite", cpus=4.0)

    _set_external(session, 1.0)

    assert docker_leases.drain_waiters(session) == [waiter.lease_id]


def test_a_small_claim_is_never_held_back_by_load(session) -> None:
    _host(session, external=6.5)
    assert _reserve(session, label="stack", cpus=1.0).granted

    assert _reserve(session, label="agent run", cpus=0.25).granted


def test_load_holds_a_claim_back_only_for_its_patience(session) -> None:
    _host(session, external=6.0)
    assert _reserve(session, label="stack", cpus=1.0).granted
    waiter = _reserve(session, label="suite", cpus=4.0)
    lease = session.get(DockerLease, waiter.lease_id)
    lease.requested_at = utcnow() - timedelta(seconds=settings.host_load_patience_seconds + 1)
    session.add(lease)
    session.commit()

    assert docker_leases.drain_waiters(session) == [waiter.lease_id]
    session.expire_all()
    assert session.get(DockerLease, waiter.lease_id).status is DockerLeaseStatus.HELD


def test_load_never_lets_the_pool_book_past_its_ceiling(session) -> None:
    _host(session, external=0.0)
    assert _reserve(session, label="suite", cpus=4.0).granted

    assert not _reserve(session, label="second suite", cpus=4.0).granted
