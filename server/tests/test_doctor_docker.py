"""What the doctor says about the capacity ledger, and why the severities differ.

The reaper never reclaims a lease it could not verify, which is correct and
which means a docker outage produces *silence* — nothing reclaimed, nothing
raised, and a pool slowly filling with leases whose holders are long gone. This
check is the thing that distinguishes a sweep with nothing to do from one that
has quietly stopped working, so its severity ladder is the contract:

- unverifiable leases → FAIL. Capacity is held and cannot be settled.
- no measurable ceiling, with requests pending → FAIL. Reservations are being
  refused right now.
- no measurable ceiling, nothing asking → WARN. An unused subsystem is not a
  fault, and failing here would turn the doctor red for every workspace that
  never touches a container.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from loregarden.models.domain import (
    DockerCeilingSource,
    DockerFootprint,
    DockerLease,
    DockerLeaseStatus,
    DoctorStatus,
)
from loregarden.services import docker_leases
from loregarden.services.doctor_docker import check_docker_capacity
from sqlmodel import Session
from tests.factories import make_workspace

HOST_INFO = {"NCPU": 8, "MemTotal": 16763441152}


def _ok_docker(args):
    return subprocess.CompletedProcess(args=[], returncode=0, stdout=json.dumps(HOST_INFO))


def _dead_docker(args):
    return subprocess.CompletedProcess(
        args=[], returncode=1, stdout="", stderr="Cannot connect to the Docker daemon"
    )


def _check(session, *, invoke):
    workspace = make_workspace(session, slug="docker-doctor")
    return check_docker_capacity(session, workspace, Path("."), invoke=invoke)


def _grant(session, *, cpus=1.0, memory_mb=1024):
    pool = docker_leases.load_pool(session)
    pool.ceiling_cpus, pool.ceiling_memory_mb, pool.ceiling_leases = 8.0, 8192, 8
    pool.ceiling_source = DockerCeilingSource.CONFIG_OVERRIDE
    session.add(pool)
    session.commit()
    reservation = docker_leases.reserve(
        session,
        holder_label="doctor test",
        footprint=DockerFootprint.CUSTOM,
        cpus=cpus,
        memory_mb=memory_mb,
    )
    assert reservation.granted
    return session.get(DockerLease, reservation.lease_id)


def test_a_measurable_machine_with_room_passes(isolated_db) -> None:
    with Session(isolated_db) as session:
        finding = _check(session, invoke=_ok_docker)
    assert finding.status is DoctorStatus.PASS
    assert "cpus" in finding.finding


def test_an_idle_ledger_with_no_docker_warns_rather_than_failing(isolated_db) -> None:
    """Failing here would turn the doctor red for every workspace that never
    touches a container — which is most of them."""
    with Session(isolated_db) as session:
        finding = _check(session, invoke=_dead_docker)
    assert finding.status is DoctorStatus.WARN
    assert finding.ok is True, "a WARN must not make the whole report not-ok"
    assert "nothing is asking" in finding.finding


def test_no_ceiling_with_requests_pending_is_a_failure(isolated_db) -> None:
    """Now it matters: something is being refused right now."""
    with Session(isolated_db) as session:
        _grant(session)
        # Wipe the ceiling so the ledger has no measurement to fall back on.
        pool = docker_leases.load_pool(session)
        pool.ceiling_cpus, pool.ceiling_memory_mb, pool.ceiling_leases = 0.0, 0, 0
        pool.ceiling_source = DockerCeilingSource.UNKNOWN
        session.add(pool)
        session.commit()

        finding = _check(session, invoke=_dead_docker)
    assert finding.status is DoctorStatus.FAIL
    assert "being refused" in finding.finding


def test_a_stale_ceiling_warns_and_names_the_error(isolated_db) -> None:
    """Reservations keep working against the last good number. That is a thing
    to know, not a thing to stop for."""
    with Session(isolated_db) as session:
        docker_leases.refresh_ceiling(session, invoke=_ok_docker, use_cache=False)
        finding = _check(session, invoke=_dead_docker)
    assert finding.status is DoctorStatus.WARN
    assert "last good measurement" in finding.finding
    assert "Cannot connect" in finding.finding


def test_a_lease_the_reaper_could_not_verify_is_a_failure(isolated_db) -> None:
    """The silence case, made loud. Capacity is held and cannot be settled, and
    nothing else in the system would ever say so."""
    with Session(isolated_db) as session:
        lease = _grant(session)
        lease.last_probe_outcome = "daemon_unreachable"
        lease.last_probe_error = "Cannot connect to the Docker daemon"
        session.add(lease)
        session.commit()

        finding = _check(session, invoke=_ok_docker)
    assert finding.status is DoctorStatus.FAIL
    assert "cannot be verified" in finding.finding
    assert finding.remediation


def test_an_orphaned_lease_warns_and_says_who_must_stop_it(isolated_db) -> None:
    with Session(isolated_db) as session:
        lease = _grant(session)
        lease.status = DockerLeaseStatus.ORPHANED
        session.add(lease)
        session.commit()

        finding = _check(session, invoke=_ok_docker)
    assert finding.status is DoctorStatus.WARN
    assert "still running" in finding.finding
    assert "never runs" in finding.remediation, (
        "the remediation must say the ledger will not stop it for you"
    )
