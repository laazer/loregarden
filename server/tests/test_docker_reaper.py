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
# Above macOS's PID_MAX of 99999, so no process can hold it on this machine.
_DEAD_PID = 999999
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
    with mock.patch.object(docker_leases, "pid_alive", return_value=False):
        report = docker_reaper.reap_docker_leases(session, invoke=NoLivenessProbe(), now=NOW)

    assert lease.id in report.reclaimed
    session.refresh(lease)
    assert lease.status is DockerLeaseStatus.RELEASED
    assert lease.end_reason is DockerLeaseEndReason.PID_GONE
    assert docker_leases.load_pool(session).held_count == 0


def test_a_live_pid_on_an_unexpired_lease_is_left_alone(session) -> None:
    lease = _hold(session, holder_pid=1, expired=False)
    with mock.patch.object(docker_leases, "pid_alive", return_value=True):
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


def test_a_waiter_that_keeps_polling_keeps_its_place(session) -> None:
    """Queued for two hours and still asking: a long wait, not an abandoned one.
    The sweep used to judge by the request time alone and dropped it."""
    _hold(session, cpus=8.0, memory_mb=8192, expired=False)
    queued = docker_leases.reserve(
        session,
        holder_label="patient",
        footprint=DockerFootprint.CUSTOM,
        cpus=1.0,
        memory_mb=1024,
    )
    waiter = session.get(DockerLease, queued.lease_id)
    waiter.requested_at = NOW - timedelta(hours=2)
    waiter.last_polled_at = NOW - timedelta(seconds=30)
    session.add(waiter)
    session.commit()

    docker_reaper.reap_docker_leases(session, invoke=NoLivenessProbe(), now=NOW)

    session.refresh(waiter)
    assert waiter.status is DockerLeaseStatus.WAITING


def test_a_waiter_whose_process_is_gone_leaves_the_line_at_once(session) -> None:
    """Killed while queued: nobody will poll again, so the cutoff is not waited out."""
    _hold(session, cpus=8.0, memory_mb=8192, expired=False)
    queued = docker_leases.reserve(
        session,
        holder_label="killed",
        footprint=DockerFootprint.CUSTOM,
        cpus=1.0,
        memory_mb=1024,
        holder_pid=4_000_000,
    )

    with mock.patch.object(docker_leases, "pid_alive", return_value=False):
        docker_reaper.reap_docker_leases(session, invoke=NoLivenessProbe(), now=NOW)

    waiter = session.get(DockerLease, queued.lease_id)
    session.refresh(waiter)
    assert waiter.status is DockerLeaseStatus.RELEASED
    assert waiter.end_reason is DockerLeaseEndReason.PID_GONE


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


def test_a_waiter_takes_back_capacity_from_a_holder_whose_process_is_gone(session) -> None:
    """The reaper runs on the reconcile timer, and a landing inside that sweep is
    the waiter: behind a holder a restart had killed, it waited its whole budget."""
    dead = _hold(session, cpus=8.0, memory_mb=8192, expired=False, holder_pid=4_000_000)
    queued = docker_leases.reserve(
        session, holder_label="landing", footprint=DockerFootprint.CUSTOM, cpus=4.0, memory_mb=4096
    )
    assert queued.position is not None

    with (
        mock.patch.object(docker_leases, "pid_alive", return_value=False),
        mock.patch.object(docker_leases.time, "sleep"),
    ):
        granted = docker_leases.wait_for_grant(session, queued, budget=5.0)

    assert granted.granted
    session.refresh(dead)
    assert dead.end_reason is DockerLeaseEndReason.PID_GONE


# ---- lg-machine-resource-1923 R6: a dead orchestrator's parent is reclaimed ---
#
# A run-scoped parent lease lives for the whole orchestration — 17 minutes on
# average, 116 at the tail — so the window in which its holder can die while it
# still holds 0.5 cpu is the longest of any lease on this machine. Both tests
# assert the children end PARENT_RELEASED and that the pool's held totals return
# to what they were before the claim: a parent reclaimed while its cover is
# still counted is a leak that looks like scarcity.


def _parent_and_child(session, *, pid: int | None, orchestration_run_id: str):
    from loregarden.models.domain import CapacityPool, DockerHolderKind
    from loregarden.services.capacity_children import reserve_child
    from tests.test_capacity_host_pool import _set_ceiling

    # Pinned rather than probed: `NoLivenessProbe` refuses anything but `info`,
    # and an unmeasured host ceiling would send the claim off to measure one.
    _set_ceiling(session, CapacityPool.HOST, cpus=7.0, memory_mb=48332, leases=6)

    parent = _hold(
        session,
        cpus=0.5,
        memory_mb=512,
        holder_kind=DockerHolderKind.ORCHESTRATION,
        orchestration_run_id=orchestration_run_id,
        holder_pid=pid,
        pool=CapacityPool.HOST,
        takes_slot=False,
    )
    child = reserve_child(
        session,
        parent_lease_id=parent.id,
        holder_label="gates plan_to_plan-synthesis",
        footprint=DockerFootprint.CUSTOM,
        cpus=1.0,
        memory_mb=1024,
        pool=CapacityPool.HOST,
    )
    assert child.granted
    return parent, session.get(DockerLease, child.lease_id)


def _host_totals(session) -> tuple[float, int]:
    from loregarden.models.domain import CapacityPool
    from loregarden.services.docker_ledger import load_pool

    session.expire_all()
    row = load_pool(session, CapacityPool.HOST)
    return (row.held_cpus, row.held_memory_mb)


def _running_orchestration(session, *, renewed_minutes_ago: int):
    from datetime import timedelta as _timedelta

    from loregarden.models.domain import OrchestrationRun, OrchestrationRunStatus
    from loregarden.models.domain.enums import utcnow
    from tests.factories import make_ticket

    workspace = make_workspace(session, slug="reaper-parent")
    ticket = make_ticket(session, workspace_id=workspace.id, external_id="lg-reaper-parent-1")
    # Real wall-clock, not NOW: `orchestration_lease_expired` compares against
    # `datetime.now`, so a 2026-09-09 stamp would read as quiet for a year and
    # make the live-run case indistinguishable from the stale one.
    stamp = utcnow() - _timedelta(minutes=renewed_minutes_ago)
    run = OrchestrationRun(
        run_code="orch_reap1",
        ticket_id=ticket.id,
        workspace_id=workspace.id,
        status=OrchestrationRunStatus.RUNNING,
        created_at=stamp,
        started_at=stamp,
        last_seen_at=stamp,
    )
    session.add(run)
    session.commit()
    session.refresh(run)
    return run


def test_a_parent_whose_pid_is_gone_is_reclaimed(session) -> None:
    """R6. The decisive case: the orchestrating process is gone.

    Nothing else can clear this. A parent names no containers, so docker cannot
    be asked about it, and the run row still says RUNNING because the process
    that would have completed it died.
    """
    before = _host_totals(session)
    run = _running_orchestration(session, renewed_minutes_ago=1)
    parent, child = _parent_and_child(session, pid=_DEAD_PID, orchestration_run_id=run.id)
    assert _host_totals(session) != before

    # Only the parent's pid is gone: the child's is this process's, and judging
    # it PID_GONE too would assert PARENT_RELEASED off the wrong cause.
    with mock.patch.object(docker_leases, "pid_alive", side_effect=lambda pid: pid != _DEAD_PID):
        report = docker_reaper.reap_docker_leases(session, invoke=NoLivenessProbe(), now=NOW)

    session.expire_all()
    assert parent.id in report.reclaimed
    assert session.get(DockerLease, parent.id).end_reason is DockerLeaseEndReason.PID_GONE
    assert session.get(DockerLease, child.id).end_reason is DockerLeaseEndReason.PARENT_RELEASED
    assert session.get(DockerLease, child.id).status is DockerLeaseStatus.RELEASED
    assert _host_totals(session) == before, "reclaiming the parent stranded its child's booking"


def test_a_parent_with_a_stale_lease_under_a_live_server_is_also_reclaimed(session) -> None:
    """R6, and the realistic leak rather than the decisive one.

    The parent's `holder_pid` is the *server's* pid, because the orchestration
    driver runs in a thread of it. So the pid is alive long after the driver
    thread has died, and `_run_is_live` judging on `OrchestrationRun.status`
    alone vouches for a run nobody is driving — extending the lease on every
    sweep, forever, silently.

    The agent branch directly above already consults
    `agent_run_lease_expired`; the orchestration branch must mirror it with
    `orchestration_lease_expired`. The run below is RUNNING, has renewed
    nothing for well past the lease, and has no agent run in flight to vouch
    for it.
    """
    import os

    from loregarden.services.run_concurrency import ORCHESTRATION_LEASE, orchestration_lease_expired

    before = _host_totals(session)
    stale_minutes = int(ORCHESTRATION_LEASE.total_seconds() // 60) + 30
    run = _running_orchestration(session, renewed_minutes_ago=stale_minutes)
    assert orchestration_lease_expired(session, run), "the scenario's own premise"
    parent, child = _parent_and_child(session, pid=os.getpid(), orchestration_run_id=run.id)

    report = docker_reaper.reap_docker_leases(session, invoke=NoLivenessProbe(), now=NOW)

    session.expire_all()
    assert parent.id in report.reclaimed, (
        "a live server's pid vouched for a dead driver thread; this lease is "
        "extended on every sweep and no clock ever recovers it"
    )
    assert parent.id not in report.renewed
    assert session.get(DockerLease, child.id).end_reason is DockerLeaseEndReason.PARENT_RELEASED
    assert _host_totals(session) == before


def test_a_parent_whose_run_is_renewing_keeps_its_lease(session) -> None:
    """R6's other side. The reaper must not free a parent out from under a live
    orchestration: a run renewing inside its lease is work in flight, and
    reclaiming its parent would make every later gate on it refuse."""
    import os

    run = _running_orchestration(session, renewed_minutes_ago=0)
    parent, child = _parent_and_child(session, pid=os.getpid(), orchestration_run_id=run.id)

    report = docker_reaper.reap_docker_leases(session, invoke=NoLivenessProbe(), now=NOW)

    session.expire_all()
    assert parent.id not in report.reclaimed
    assert parent.id in report.renewed
    assert session.get(DockerLease, child.id).status is DockerLeaseStatus.HELD
