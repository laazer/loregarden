"""The reap decision, one test per branch, in the order the reaper asks.

The branch that matters most is the last one. `docker ps` prints nothing both
when no container matched and when the daemon could not be reached, so a probe
layer that returned a count would make "everything stopped" and "Docker Desktop
is restarting" the same answer — and the reaper would free every lease on the
machine at exactly the moment it can prove nothing. Three of the tests here
exist for that single confusion.

The other invariant pinned here: evidence is consulted cheapest-first. A dead
pid and a live run are both decided without spawning anything, and the tests
assert the fake was never invoked rather than merely that the outcome was right —
an implementation that probed first and ignored the answer would pass on outcome
alone while making every sweep pay for a subprocess per lease.
"""

from __future__ import annotations

import json
import subprocess
from datetime import datetime, timedelta, timezone
from unittest import mock

import pytest
from loregarden.models.domain import (
    DockerCeilingSource,
    DockerFootprint,
    DockerLease,
    DockerLeaseEndReason,
    DockerLeaseStatus,
    DockerLivenessState,
    DockerProbeOutcome,
    RunStatus,
)
from loregarden.services import docker_leases, docker_reaper
from loregarden.services.docker_probe import DockerLiveness
from sqlmodel import Session
from tests.factories import make_agent_run, make_workspace

NOW = datetime(2026, 9, 9, 12, 0, tzinfo=timezone.utc)
#: This machine, as measured, for the ceiling refresh the sweep performs.
HOST_INFO = {"NCPU": 8, "MemTotal": 16763441152}


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
    return pool


class NoLivenessProbe:
    """Serves `docker info`, and fails the test if anything asks about a container.

    The property being pinned is that judging a lease by a dead pid, or by the
    run behind it, costs no liveness probe — that is a property of the decision
    *order*, and an outcome-only assertion cannot see it. The sweep does refresh
    the capacity ceiling once (behind the probe cache), so refusing every docker
    call outright would pin something stronger than the design claims and break
    the moment a legitimate `info` was added — as it did.
    """

    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def __call__(self, args):
        argv = list(args)
        self.calls.append(argv)
        if argv and argv[0] == "info":
            return subprocess.CompletedProcess(
                args=argv, returncode=0, stdout=json.dumps(HOST_INFO), stderr=""
            )
        raise AssertionError(f"a liveness probe ran when none should have: {argv}")


def _hold(session, *, cpus=1.0, memory_mb=1024, expired=True, **fields) -> DockerLease:
    reservation = docker_leases.reserve(
        session,
        holder_label="test",
        footprint=DockerFootprint.CUSTOM,
        cpus=cpus,
        memory_mb=memory_mb,
        **fields,
    )
    assert reservation.granted
    lease = session.get(DockerLease, reservation.lease_id)
    lease.expires_at = (NOW - timedelta(minutes=1)) if expired else (NOW + timedelta(minutes=30))
    session.add(lease)
    session.commit()
    return lease


def _fake_probe(liveness: DockerLiveness):
    return mock.patch.object(docker_reaper, "probe_lease_liveness", return_value=liveness)


# ---- 1: a dead pid settles it with no docker call ----------------------


def test_a_gone_pid_is_decisive_and_costs_no_subprocess(session) -> None:
    lease = _hold(session, holder_pid=999999, expired=False)
    with mock.patch.object(docker_reaper, "pid_alive", return_value=False):
        report = docker_reaper.reap_docker_leases(session, invoke=NoLivenessProbe(), now=NOW)

    assert lease.id in report.reclaimed
    session.refresh(lease)
    assert lease.status is DockerLeaseStatus.RELEASED
    assert lease.end_reason is DockerLeaseEndReason.PID_GONE
    assert docker_leases.load_pool(session).held_count == 0


def test_a_live_pid_on_an_unexpired_lease_is_left_alone(session) -> None:
    lease = _hold(session, holder_pid=1, expired=False)
    with mock.patch.object(docker_reaper, "pid_alive", return_value=True):
        report = docker_reaper.reap_docker_leases(session, invoke=NoLivenessProbe(), now=NOW)
    assert not report.reclaimed
    session.refresh(lease)
    assert lease.status is DockerLeaseStatus.HELD


# ---- 2: a live run behind the lease vouches for it ---------------------


def test_an_expired_lease_whose_run_is_still_working_is_extended_not_reaped(session) -> None:
    """The lease inherits the run's heartbeat rather than carrying a second one
    that can drift out of step with it."""
    workspace = make_workspace(session, slug="reaper-ws")
    run = make_agent_run(session, workspace_id=workspace.id, status=RunStatus.RUNNING)
    lease = _hold(session, agent_run_id=run.id)

    report = docker_reaper.reap_docker_leases(session, invoke=NoLivenessProbe(), now=NOW)

    assert lease.id in report.renewed
    assert not report.reclaimed
    session.refresh(lease)
    assert lease.status is DockerLeaseStatus.HELD
    assert docker_leases.as_utc(lease.expires_at) > NOW


def test_an_expired_lease_whose_run_has_finished_is_reaped(session) -> None:
    workspace = make_workspace(session, slug="reaper-ws-2")
    run = make_agent_run(session, workspace_id=workspace.id, status=RunStatus.SUCCEEDED)
    lease = _hold(session, agent_run_id=run.id)

    report = docker_reaper.reap_docker_leases(session, invoke=NoLivenessProbe(), now=NOW)

    assert lease.id in report.reclaimed
    session.refresh(lease)
    assert lease.end_reason is DockerLeaseEndReason.TTL_EXPIRED


# ---- 3: nothing to ask docker about ------------------------------------


def test_an_expired_lease_naming_no_containers_is_reaped_on_its_clock(session) -> None:
    """The ad-hoc caller who reserved and walked away. There is nothing docker
    could confirm, so the clock is the only evidence and it is decisive."""
    lease = _hold(session)
    report = docker_reaper.reap_docker_leases(session, invoke=NoLivenessProbe(), now=NOW)

    assert lease.id in report.reclaimed
    session.refresh(lease)
    assert lease.end_reason is DockerLeaseEndReason.TTL_EXPIRED


# ---- 4: docker's answer ------------------------------------------------


def test_containers_confirmed_gone_releases_the_capacity(session) -> None:
    lease = _hold(session, cpus=2.0, memory_mb=2048)
    lease.compose_project = "myapp"
    session.add(lease)
    session.commit()

    with _fake_probe(DockerLiveness(DockerLivenessState.GONE)):
        report = docker_reaper.reap_docker_leases(session, now=NOW)

    assert lease.id in report.reclaimed
    session.refresh(lease)
    assert lease.end_reason is DockerLeaseEndReason.CONTAINERS_GONE
    assert lease.last_probe_outcome == DockerProbeOutcome.OK.value
    assert docker_leases.load_pool(session).held_cpus == 0.0


def test_containers_still_running_hold_the_capacity_and_are_surfaced(session) -> None:
    """This ledger never stops anything — a human does. Freeing here would
    double-book a machine that is genuinely busy."""
    lease = _hold(session, cpus=2.0, memory_mb=2048)
    lease.compose_project = "myapp"
    lease.container_names_json = json.dumps(["myapp-db-1"])
    session.add(lease)
    session.commit()

    with _fake_probe(DockerLiveness(DockerLivenessState.ALIVE, running=3)):
        report = docker_reaper.reap_docker_leases(session, now=NOW)

    assert lease.id not in report.reclaimed
    assert lease.id in report.orphaned
    session.refresh(lease)
    assert lease.status is DockerLeaseStatus.ORPHANED
    assert lease.running_container_count == 3
    assert docker_leases.load_pool(session).held_cpus == 2.0, "an orphan still occupies the pool"


def test_an_orphan_that_starts_renewing_again_returns_to_held(session) -> None:
    lease = _hold(session)
    lease.compose_project = "myapp"
    session.add(lease)
    session.commit()
    with _fake_probe(DockerLiveness(DockerLivenessState.ALIVE, running=1)):
        docker_reaper.reap_docker_leases(session, now=NOW)
    session.refresh(lease)
    assert lease.status is DockerLeaseStatus.ORPHANED

    assert docker_leases.renew_lease(session, lease.id) is not None
    session.refresh(lease)
    assert lease.status is DockerLeaseStatus.HELD


@pytest.mark.parametrize(
    "outcome",
    [
        DockerProbeOutcome.DAEMON_UNREACHABLE,
        DockerProbeOutcome.BINARY_MISSING,
        DockerProbeOutcome.TIMED_OUT,
    ],
)
def test_a_failed_probe_never_reads_as_gone(session, outcome: DockerProbeOutcome) -> None:
    """The core of the whole design. `docker ps` prints nothing both when
    nothing matched and when it could not ask; if those collapse, one Docker
    Desktop restart frees every lease on the machine."""
    lease = _hold(session, cpus=2.0, memory_mb=2048)
    lease.compose_project = "myapp"
    session.add(lease)
    session.commit()

    with _fake_probe(DockerLiveness(DockerLivenessState.UNKNOWN, outcome=outcome, error="boom")):
        report = docker_reaper.reap_docker_leases(session, now=NOW)

    assert lease.id not in report.reclaimed
    assert lease.id in report.unverifiable
    assert report.probe_errors[lease.id] == "boom"
    assert not report.healthy, "a sweep that could not verify must not look like a clean one"
    session.refresh(lease)
    assert lease.status is DockerLeaseStatus.HELD
    assert lease.last_probe_error == "boom"
    assert docker_leases.load_pool(session).held_cpus == 2.0


def test_a_clean_sweep_and_a_blind_one_are_distinguishable(session) -> None:
    """Both reclaim nothing. Only one of them needs somebody's attention, and
    `healthy` is what tells them apart."""
    empty = docker_reaper.reap_docker_leases(session, invoke=NoLivenessProbe(), now=NOW)
    assert empty.reclaimed == [] and empty.healthy is True


# ---- waiters and promotion --------------------------------------------


def test_a_waiter_nobody_is_polling_for_is_dropped_from_the_line(session) -> None:
    """The cost of strict head-of-line, paid here rather than by weakening the
    ordering: an abandoned waiter would wedge everything behind it."""
    _hold(session, cpus=8.0, memory_mb=8192, expired=False)
    queued = docker_leases.reserve(
        session,
        holder_label="abandoned",
        footprint=DockerFootprint.CUSTOM,
        cpus=1.0,
        memory_mb=1024,
    )
    stale = session.get(DockerLease, queued.lease_id)
    stale.requested_at = NOW - timedelta(hours=2)
    session.add(stale)
    session.commit()

    docker_reaper.reap_docker_leases(session, invoke=NoLivenessProbe(), now=NOW)

    session.refresh(stale)
    assert stale.status is DockerLeaseStatus.RELEASED
    assert stale.end_reason is DockerLeaseEndReason.ABANDONED


def test_reclaimed_capacity_starts_what_was_waiting_for_it(session) -> None:
    holder = _hold(session, cpus=8.0, memory_mb=8192)
    waiting = docker_leases.reserve(
        session, holder_label="next", footprint=DockerFootprint.CUSTOM, cpus=4.0, memory_mb=4096
    )
    assert waiting.position is not None

    report = docker_reaper.reap_docker_leases(session, invoke=NoLivenessProbe(), now=NOW)

    assert holder.id in report.reclaimed
    assert waiting.lease_id in report.promoted
    assert session.get(DockerLease, waiting.lease_id).status is DockerLeaseStatus.HELD
