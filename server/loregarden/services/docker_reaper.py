"""Reclaiming leases whose holders stopped saying they were alive.

A TTL alone cannot tell a holder that died from one that is mid-way through a
slow `docker compose up`, and getting that wrong in the reclaiming direction
frees capacity a live stack is using — the exact over-booking this ledger
exists to prevent. So expiry opens a question rather than settling one, and the
answer comes from whatever evidence is strongest:

1. **A recorded pid that is gone.** Decisive, with no docker call at all.
2. **A still-live run behind the lease.** The lease inherits that run's existing
   heartbeat rather than carrying a second one that can drift out of step with
   it. This is what makes the orchestrator path correct: `run_lease` already
   renews from the supervising thread, across all three execution paths, and a
   lease scoped to the same block is alive exactly when that thread is.
3. **Nothing docker could be asked about.** A lease that never bound a project
   or a container has only its clock, and the clock is then decisive.
4. **Docker's answer**, for a lease that did bind.

Step 4 has three outcomes and the third is the one that matters:

- containers gone → reclaim.
- containers running → **do not reclaim**, extend the grace window, and say so
  loudly. This ledger never stops anything; a human does. Silently reclaiming
  here would double-book a machine that is genuinely busy.
- the probe itself failed → **do not reclaim, and never read as "gone"**.
  ``docker ps`` with no match and ``docker ps`` against a dead daemon both
  print nothing; a handler that collapsed them would free every lease on the
  box the moment Docker Desktop restarted. The failure is recorded on the lease
  and surfaced through the doctor check, because a reaper that has quietly
  stopped reaping looks exactly like one with nothing to do.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from loregarden.config import settings
from loregarden.models.domain import (
    AgentRun,
    DockerLease,
    DockerLeaseEndReason,
    DockerLeaseStatus,
    DockerLivenessState,
    OrchestrationRun,
)
from loregarden.services.docker_capacity import DockerInvoke
from loregarden.services.docker_leases import (
    drain_waiters,
    refresh_ceiling,
    release_lease,
    repair_pool,
)
from loregarden.services.docker_ledger import OCCUPYING, as_utc, container_names
from loregarden.services.docker_probe import DockerLiveness, probe_lease_liveness
from loregarden.services.docker_subprocess import run_docker
from loregarden.services.parallel_queue import (
    LIVE_ORCHESTRATION_STATUSES,
    LIVE_RUN_STATUSES,
)
from loregarden.services.run_lease import agent_run_lease_expired, pid_alive
from sqlmodel import Session, func, select

logger = logging.getLogger(__name__)


@dataclass
class ReapReport:
    """What the sweep did, and — more importantly — what it could not do.

    `unverifiable` is the field that exists for the no-silent-failures rule: a
    sweep that reclaimed nothing because docker was unreachable and a sweep that
    reclaimed nothing because everything is healthy are the same empty list
    otherwise, and only one of them needs somebody's attention.
    """

    reclaimed: list[str] = field(default_factory=list)
    renewed: list[str] = field(default_factory=list)
    orphaned: list[str] = field(default_factory=list)
    unverifiable: list[str] = field(default_factory=list)
    promoted: list[str] = field(default_factory=list)
    probe_errors: dict[str, str] = field(default_factory=dict)

    @property
    def healthy(self) -> bool:
        return not self.unverifiable

    def as_dict(self) -> dict:
        return {
            "reclaimed": list(self.reclaimed),
            "renewed": list(self.renewed),
            "orphaned": list(self.orphaned),
            "unverifiable": list(self.unverifiable),
            "promoted": list(self.promoted),
            "probe_errors": dict(self.probe_errors),
        }


def _run_is_live(session: Session, lease: DockerLease) -> bool:
    """Whether the run behind this lease is still working.

    Fails closed in the same direction `run_lease.run_has_renewer` does: a run
    row that exists and is in flight keeps its lease, and a run whose lease has
    itself expired does not get to vouch for anything.
    """
    if lease.agent_run_id:
        run = session.get(AgentRun, lease.agent_run_id)
        if run is None:
            return False
        return run.status in LIVE_RUN_STATUSES and not agent_run_lease_expired(session, run)
    if lease.orchestration_run_id:
        orchestration = session.get(OrchestrationRun, lease.orchestration_run_id)
        return orchestration is not None and orchestration.status in LIVE_ORCHESTRATION_STATUSES
    return False


def _has_docker_identity(lease: DockerLease) -> bool:
    return bool(lease.compose_project) or bool(container_names(lease))


def _record_probe(lease: DockerLease, probe: DockerLiveness, *, now: datetime) -> None:
    lease.last_probe_at = now
    lease.last_probe_outcome = probe.outcome.value
    lease.last_probe_error = probe.error
    lease.running_container_count = probe.running


def _extend(lease: DockerLease, *, now: datetime) -> None:
    """Push the expiry out by the grace window, without renewing the TTL.

    Not `renew_lease`: this is not evidence the holder is alive, only that the
    reaper has no grounds to act. Recording it as a renewal would make a
    lease with a dead holder and a live container immortal *and* invisible.
    """
    lease.expires_at = now + timedelta(seconds=settings.docker_lease_orphan_grace_seconds)


def _expired(lease: DockerLease, *, now: datetime) -> bool:
    expiry = as_utc(lease.expires_at)
    return expiry is not None and expiry <= now


def reap_docker_leases(
    session: Session,
    *,
    invoke: DockerInvoke = run_docker,
    now: datetime | None = None,
) -> ReapReport:
    """One sweep over the ledger. Never raises; returns what it found.

    Ordered as the module docstring describes, cheapest and most decisive
    evidence first, so a dead pid costs no subprocess and a live run costs no
    subprocess either.
    """
    stamp = now or datetime.now(timezone.utc)
    report = ReapReport()

    if not settings.docker_capacity_enabled:
        return report

    leases = list(session.exec(select(DockerLease).where(DockerLease.status.in_(OCCUPYING))).all())
    waiting_count = session.exec(
        select(func.count())
        .select_from(DockerLease)
        .where(DockerLease.status == DockerLeaseStatus.WAITING)
    ).one()
    if not leases and not waiting_count:
        # Nothing to judge and nothing to promote. Returning here keeps an idle
        # ledger from bumping the pool's revision on every tick of a 30-second
        # timer — a write per sweep forever, to record that nothing happened.
        return report

    # Re-measure while there is something to measure for. Behind the probe cache,
    # so this is one `docker info` every few minutes rather than one per sweep —
    # and without it a machine whose Docker allocation changed would enforce the
    # ceiling it had at boot forever.
    refresh_ceiling(session, invoke=invoke)

    for lease in leases:
        outcome = _judge(session, lease, invoke=invoke, now=stamp, report=report)
        if outcome is not None:
            release_lease(session, lease.id, reason=outcome, drain=False)
            report.reclaimed.append(lease.id)

    _drop_abandoned_waiters(session, now=stamp, report=report)
    repair_pool(session)
    report.promoted.extend(drain_waiters(session))

    if report.unverifiable:
        logger.warning(
            "Docker liveness could not be established for %d lease(s); "
            "none of them were reclaimed: %s",
            len(report.unverifiable),
            report.probe_errors,
        )
    return report


def _judge(
    session: Session,
    lease: DockerLease,
    *,
    invoke: DockerInvoke,
    now: datetime,
    report: ReapReport,
) -> DockerLeaseEndReason | None:
    """The end reason for this lease, or None to leave it alone."""
    # 1. A pid that is gone settles it outright, with no waiting and no docker.
    if lease.holder_pid is not None and not pid_alive(lease.holder_pid):
        return DockerLeaseEndReason.PID_GONE

    if not _expired(lease, now=now):
        return None

    # 2. The run behind the lease is still working, and it renews a heartbeat
    #    this lease can inherit rather than duplicating.
    if _run_is_live(session, lease):
        _extend(lease, now=now)
        session.add(lease)
        session.commit()
        report.renewed.append(lease.id)
        return None

    # 3. Nothing docker could be asked about; the clock is all there is.
    if not _has_docker_identity(lease):
        return DockerLeaseEndReason.TTL_EXPIRED

    # 4. Ask docker.
    probe = probe_lease_liveness(
        compose_project=lease.compose_project, container_names=container_names(lease), invoke=invoke
    )
    _record_probe(lease, probe, now=now)

    if probe.state is DockerLivenessState.GONE:
        session.add(lease)
        session.commit()
        return DockerLeaseEndReason.CONTAINERS_GONE

    if probe.state is DockerLivenessState.ALIVE:
        # Held open on purpose. The machine really is this busy, and this ledger
        # does not stop anything — an operator does.
        lease.status = DockerLeaseStatus.ORPHANED
        _extend(lease, now=now)
        session.add(lease)
        session.commit()
        report.orphaned.append(lease.id)
        logger.warning(
            "Lease %s (%s) expired but %d of its containers are still running; "
            "holding the capacity and not reclaiming it. Stop it with "
            "`docker compose -p %s down` if it is stale.",
            lease.id,
            lease.holder_label,
            probe.running,
            lease.compose_project or "<no project>",
        )
        return None

    # The probe itself failed. Not "gone" — never "gone".
    _extend(lease, now=now)
    session.add(lease)
    session.commit()
    report.unverifiable.append(lease.id)
    report.probe_errors[lease.id] = probe.error or probe.outcome.value
    return None


def _drop_abandoned_waiters(session: Session, *, now: datetime, report: ReapReport) -> None:
    """Take waiters nobody is polling for out of the line.

    Without this a caller that asked once and walked away wedges the head of the
    queue against everything behind it — the cost of strict head-of-line, paid
    back here rather than by weakening the ordering.
    """
    cutoff = timedelta(seconds=settings.docker_waiting_ttl_seconds)
    waiting = list(
        session.exec(
            select(DockerLease).where(DockerLease.status == DockerLeaseStatus.WAITING)
        ).all()
    )
    for lease in waiting:
        last_seen = as_utc(lease.last_renewed_at) or as_utc(lease.requested_at)
        if last_seen is not None and now - last_seen > cutoff:
            release_lease(session, lease.id, reason=DockerLeaseEndReason.ABANDONED, drain=False)
            report.reclaimed.append(lease.id)
