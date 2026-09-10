"""Containers nobody booked — the only signal that distinguishes use from disuse.

The ledger is advisory, so every other part of the feature reads identically
whether callers are booking correctly or ignoring it entirely: capacity shows
free, and the reaper reclaims nothing. This is what tells those apart, which
makes two of its properties load-bearing:

- **A failed probe is not a clean bill of health.** `docker ps` prints nothing
  both when nothing matched and when it could not ask, so an empty list with a
  failed outcome must never read as "everything is accounted for".
- **The baseline is explicit, not inferred.** No age heuristic, no guess about
  what "looks like agent work". A standing database reported as a violation on
  every sweep forever is noise, and noise is how a check stops being read.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest import mock

import pytest
from loregarden.models.domain import (
    DockerCeilingSource,
    DockerFootprint,
    DockerProbeOutcome,
    DoctorStatus,
    Workspace,
)
from loregarden.services import docker_leases
from loregarden.services.docker_unaccounted import unaccounted_containers
from loregarden.services.doctor_docker import check_docker_unaccounted
from sqlmodel import Session
from tests.factories import make_workspace

COMPOSE_LABEL = "com.docker.compose.project"


def _ps(*containers: dict):
    """A fake `docker ps --format '{{json .}}'`."""
    body = "\n".join(json.dumps(c) for c in containers)
    return lambda args: subprocess.CompletedProcess(args=list(args), returncode=0, stdout=body)


def _container(name: str, *, project: str = "", image: str = "img") -> dict:
    return {
        "Names": name,
        "Labels": f"{COMPOSE_LABEL}={project}" if project else "some.other.label=x",
        "Image": image,
        "CreatedAt": "2026-09-10 09:14:31 +0000 UTC",
    }


@pytest.fixture(name="session")
def session_fixture(isolated_db):
    with Session(isolated_db) as session:
        yield session


@pytest.fixture(name="ceiling", autouse=True)
def ceiling_fixture(session):
    pool = docker_leases.load_pool(session)
    pool.ceiling_cpus = 8.0
    pool.ceiling_memory_mb = 8192
    pool.ceiling_leases = 8
    pool.ceiling_source = DockerCeilingSource.CONFIG_OVERRIDE
    session.add(pool)
    session.commit()


def _hold(session, label: str, *, project: str = "", names: list[str] | None = None):
    reservation = docker_leases.reserve(
        session, holder_label=label, footprint=DockerFootprint.LIGHT
    )
    assert reservation.granted
    reservation.bind(compose_project=project, container_names=names or [])
    return reservation


def test_a_container_no_lease_claims_is_reported(session) -> None:
    report = unaccounted_containers(session, invoke=_ps(_container("rogue", project="mystery")))
    assert report.ok
    assert [c.Names for c in report.containers] == ["rogue"]
    assert report.projects == ["mystery"]


def test_a_container_a_lease_claims_by_project_is_not_reported(session) -> None:
    _hold(session, "booked", project="myapp")
    report = unaccounted_containers(session, invoke=_ps(_container("myapp-db-1", project="myapp")))
    assert report.containers == []


def test_a_container_a_lease_claims_by_name_is_not_reported(session) -> None:
    _hold(session, "booked", names=["lonely-container"])
    report = unaccounted_containers(session, invoke=_ps(_container("lonely-container")))
    assert report.containers == []


def test_the_configured_baseline_is_not_reported(session) -> None:
    """A standing database is not a violation, and reporting it every sweep
    forever is how this check would get ignored."""
    invoke = _ps(
        _container("kairos-postgres", project="server"),
        _container("my-redis"),
        _container("rogue", project="mystery"),
    )
    with mock.patch.multiple(
        "loregarden.services.docker_unaccounted.settings",
        docker_baseline_projects="server",
        docker_baseline_containers="my-redis",
    ):
        report = unaccounted_containers(session, invoke=invoke)
    assert [c.Names for c in report.containers] == ["rogue"]


def test_a_failed_probe_is_not_a_clean_bill_of_health(session) -> None:
    """`docker ps` prints nothing both when nothing matched and when it could not
    ask. An empty list must carry which one it was."""

    def dead(args):
        return subprocess.CompletedProcess(
            args=list(args), returncode=1, stdout="", stderr="Cannot connect to the Docker daemon"
        )

    report = unaccounted_containers(session, invoke=dead)
    assert report.containers == []
    assert report.ok is False
    assert report.outcome is DockerProbeOutcome.DAEMON_UNREACHABLE
    assert "Cannot connect" in report.error


def test_an_unreadable_ps_line_is_skipped_loudly_not_silently(session, caplog) -> None:
    def noisy(args):
        return subprocess.CompletedProcess(
            args=list(args),
            returncode=0,
            stdout="not json\n" + json.dumps(_container("rogue")),
        )

    with caplog.at_level("WARNING"):
        report = unaccounted_containers(session, invoke=noisy)
    assert [c.Names for c in report.containers] == ["rogue"]
    assert any("docker ps" in record.message for record in caplog.records)


def test_disabling_the_ledger_reports_nothing(session) -> None:
    with mock.patch.object(
        __import__("loregarden.services.docker_unaccounted", fromlist=["settings"]).settings,
        "docker_capacity_enabled",
        False,
    ):
        report = unaccounted_containers(session, invoke=_ps(_container("rogue")))
    assert report.ok and report.containers == []


# ---- the doctor check ---------------------------------------------------


def _check(session, invoke) -> object:
    workspace: Workspace = make_workspace(session, slug="unaccounted-ws")
    return check_docker_unaccounted(session, workspace, Path("."), invoke=invoke)


def test_the_check_passes_when_everything_is_booked(session) -> None:
    _hold(session, "booked", project="myapp")
    finding = _check(session, _ps(_container("myapp-db-1", project="myapp")))
    assert finding.status is DoctorStatus.PASS


def test_the_check_warns_and_names_what_it_found(session) -> None:
    finding = _check(session, _ps(_container("rogue", project="mystery")))
    assert finding.status is DoctorStatus.WARN
    assert "rogue" in finding.finding
    assert "mystery" in finding.remediation, "the remedy must name the project to allowlist"
    assert "skipped loregarden_reserve_docker_capacity" in finding.remediation, (
        "the other reading — an agent that bypassed the ledger — must be offered too"
    )


def test_the_check_warns_rather_than_passing_when_docker_cannot_be_asked(session) -> None:
    def dead(args):
        return subprocess.CompletedProcess(args=list(args), returncode=1, stdout="", stderr="down")

    finding = _check(session, dead)
    assert finding.status is DoctorStatus.WARN
    assert "unknown" in finding.finding


def test_the_check_never_fails_a_report_only_condition(session) -> None:
    """It stops nothing and reclaims nothing, and a FAIL here would block work
    over somebody's personal database."""
    for invoke in (_ps(_container("rogue")), _ps()):
        assert _check(session, invoke).status is not DoctorStatus.FAIL
