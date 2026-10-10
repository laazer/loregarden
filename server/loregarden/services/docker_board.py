"""What the docker capacity ledger looks like right now, as one payload.

Its own module rather than more of `docker_leases`, which is the write path.
This is the read: who holds what, who is waiting, and — the part a bare
"3 of 4 leases used" would leave out — how much the number quoting that ratio
can be trusted.

A ceiling derived from a probe that failed an hour ago is still the right number
to enforce, and it is not the same claim as one measured a minute ago. Every
payload here carries `ceiling.source`, so a caller can tell them apart. The
same applies to leases the reaper could not verify: they are listed separately
rather than folded into the holders, because "held by a live stack" and "held
because docker could not be reached" need different responses from whoever is
reading.
"""

from __future__ import annotations

from datetime import datetime, timezone

from loregarden.config import settings
from loregarden.models.domain import (
    AgentRun,
    CapacityPool,
    DockerCapacityPool,
    DockerLease,
    DockerLeaseStatus,
    DockerProbeOutcome,
)
from loregarden.services.capacity_label import parse_holder_label
from loregarden.services.capacity_progress import describe_progress
from loregarden.services.docker_capacity import Ceiling, DockerInvoke
from loregarden.services.docker_leases import refresh_ceiling
from loregarden.services.docker_ledger import (
    CHARGED_POOLS,
    OCCUPYING,
    as_utc,
    container_names,
    last_seen_at,
    load_pool,
    pool_ceiling,
    shortfalls,
)
from loregarden.services.docker_subprocess import run_docker
from loregarden.services.docker_wait_estimate import (
    UNKNOWN_WAIT,
    WaitEstimate,
    estimate_waits,
)
from sqlmodel import Session, select


def _stall_after_seconds() -> float:
    """How long a waiter may go unseen before the board calls it stalled.

    A live waiter polls at least every `docker_poll_max_interval_seconds`, so
    two of those with no sign of it means it has most likely stopped — well
    before the abandonment sweep drops it at `docker_waiting_ttl_seconds`.
    """
    return 2 * settings.docker_poll_max_interval_seconds


def _waiting_payload(lease: DockerLease, *, now: datetime) -> dict:
    """How long a waiter has queued, and whether it is still asking.

    Age alone cannot tell a stuck waiter from a patient one at the back of a
    long line; whether its polls stopped can.
    """
    requested_at = as_utc(lease.requested_at)
    seen = last_seen_at(lease)
    unseen = int((now - seen).total_seconds()) if seen else None
    return {
        "waiting_seconds": (int((now - requested_at).total_seconds()) if requested_at else None),
        "last_seen_seconds_ago": unseen,
        "poll_stalled": unseen is not None and unseen > _stall_after_seconds(),
        # When the abandonment sweep will take it out of the line unless it polls.
        "drops_in_seconds": (
            max(0, settings.docker_waiting_ttl_seconds - unseen) if unseen is not None else None
        ),
    }


_NOT_WAITING = {
    "waiting_seconds": None,
    "last_seen_seconds_ago": None,
    "poll_stalled": False,
    "drops_in_seconds": None,
}


def _run_tickets(session: Session, leases: list[DockerLease]) -> dict[str, str]:
    """The ticket each lease's agent run belongs to, for leases naming a run only."""
    run_ids = {lease.agent_run_id for lease in leases if lease.agent_run_id and not lease.ticket_id}
    if not run_ids:
        return {}
    rows = session.exec(
        select(AgentRun.id, AgentRun.ticket_id).where(AgentRun.id.in_(run_ids))
    ).all()
    return {run_id: ticket_id for run_id, ticket_id in rows if ticket_id}


def _progress_payload(lease: DockerLease) -> dict | None:
    """The step and count the held command last reported; None if it never has."""
    if not lease.progress_step:
        return None
    reported_at = as_utc(lease.progress_at)
    return {
        "step": lease.progress_step,
        "done": lease.progress_done,
        "total": lease.progress_total,
        "summary": describe_progress(
            lease.progress_step, lease.progress_done, lease.progress_total
        ),
        "reported_at": reported_at.isoformat() if reported_at else None,
    }


def _lease_payload(
    lease: DockerLease,
    *,
    now: datetime,
    run_tickets: dict[str, str],
    place: int | None = None,
    estimate: WaitEstimate | None = None,
) -> dict:
    expires_at = as_utc(lease.expires_at)
    requested_at = as_utc(lease.requested_at)
    granted_at = as_utc(lease.granted_at) if lease.status is not DockerLeaseStatus.WAITING else None
    return {
        "lease_id": lease.id,
        "status": lease.status.value,
        "holder_label": lease.holder_label,
        # The label read back as what / where / pid, so a reader does not have
        # to re-split a string whose shape has changed over time.
        "holder": parse_holder_label(lease.holder_label, pid=lease.holder_pid).as_dict(),
        "holder_kind": lease.holder_kind.value,
        "pool": lease.pool.value,
        "parent_lease_id": lease.parent_lease_id,
        "covered_cpus": lease.covered_cpus,
        "covered_memory_mb": lease.covered_memory_mb,
        "agent_run_id": lease.agent_run_id,
        # A stage's lease names its run; the ticket is where the run is read.
        "ticket_id": lease.ticket_id or run_tickets.get(lease.agent_run_id or ""),
        "footprint": lease.footprint.value,
        "cpus": lease.cpus,
        "memory_mb": lease.memory_mb,
        "compose_project": lease.compose_project,
        "container_names": container_names(lease),
        # Place in line, 1-based, for a waiter; None for a holder. Not
        # `lease.position`, which is a ticket from a counter that never resets
        # — the board once numbered a line of seven 72 to 78.
        "position": place,
        **(estimate or UNKNOWN_WAIT).as_dict(),
        "poll_count": lease.poll_count,
        "requested_at": requested_at.isoformat() if requested_at else None,
        # How long a holder has run, and how far it says it has got. A push is
        # mostly one long test suite; "holding" alone cannot tell minute two
        # from minute forty.
        "held_seconds": (int((now - granted_at).total_seconds()) if granted_at else None),
        "progress": _progress_payload(lease),
        **(
            _waiting_payload(lease, now=now)
            if lease.status is DockerLeaseStatus.WAITING
            else _NOT_WAITING
        ),
        "expires_at": expires_at.isoformat() if expires_at else None,
        "expires_in_seconds": (int((expires_at - now).total_seconds()) if expires_at else None),
        # What the last probe actually found. The attention panel needs the
        # count, not just the status: "expired but 3 containers still up" is a
        # different thing to act on than "expired, nothing running".
        "running_container_count": lease.running_container_count,
        "last_probe_outcome": lease.last_probe_outcome,
        "last_probe_error": lease.last_probe_error,
    }


def capacity_status(
    session: Session,
    *,
    now: datetime | None = None,
    invoke: DockerInvoke = run_docker,
    measure_if_unknown: bool = True,
) -> dict:
    """The whole board: ceiling, holders, the line, and what could not be verified.

    Measures once if the ledger has never been measured. Without that, the
    first thing anyone runs reports a ceiling of zero from the `unknown`
    default and reads as a broken feature on a machine where docker is
    perfectly healthy. Afterwards the reap sweep keeps the number current, so
    this stays a read on every subsequent call.
    """
    stamp = now or datetime.now(timezone.utc)
    pool = load_pool(session)
    ceiling = pool_ceiling(pool)
    if measure_if_unknown and not ceiling.known:
        ceiling = refresh_ceiling(session, invoke=invoke)
        pool = load_pool(session)
    host = load_pool(session, CapacityPool.HOST)
    host_ceiling = pool_ceiling(host)
    if measure_if_unknown and not host_ceiling.known:
        host_ceiling = refresh_ceiling(session, pool_name=CapacityPool.HOST)
        host = load_pool(session, CapacityPool.HOST)

    holders = list(
        session.exec(
            select(DockerLease)
            .where(DockerLease.status.in_(OCCUPYING))
            .order_by(DockerLease.granted_at)
        ).all()
    )
    waiting = list(
        session.exec(
            select(DockerLease)
            .where(DockerLease.status == DockerLeaseStatus.WAITING)
            .order_by(DockerLease.position)
        ).all()
    )

    # A lease the reaper could not verify is not a healthy holder. Listing it
    # under both is deliberate: it occupies capacity (so it belongs in the
    # totals) and it needs attention (so it must not be invisible).
    unverifiable = [
        lease
        for lease in holders
        if lease.last_probe_outcome and lease.last_probe_outcome != DockerProbeOutcome.OK.value
    ]

    # Estimated once for the whole board rather than per entry: the projection
    # is a single walk of the queue, and asking it per waiter would re-simulate
    # the same queue N times and — worse — let two entries disagree about when
    # the same holder releases.
    estimates = estimate_waits(session, now=stamp)

    # What the head of the line is short of, from admission's own test. The rest
    # of the line waits on the head (strict order), so only its reason is news.
    # None with nobody waiting; an empty list means it fits and is about to start.
    head_shortfall = None
    if waiting:
        head = waiting[0]
        rows = {CapacityPool.DOCKER: pool, CapacityPool.HOST: host}
        head_shortfall = [
            gap.as_dict()
            for charged in CHARGED_POOLS[head.pool]
            for gap in shortfalls(charged, rows[charged], head)
        ]

    run_tickets = _run_tickets(session, holders + waiting)

    return {
        "enabled": True,
        # The top level is the docker pool, as it was before the host pool
        # existed; `host` is the machine, which docker claims are charged to too.
        **_pool_summary(pool, ceiling),
        "host": _pool_summary(host, host_ceiling),
        "holders": [_lease_payload(lease, now=stamp, run_tickets=run_tickets) for lease in holders],
        "waiting": [
            _lease_payload(
                lease,
                now=stamp,
                run_tickets=run_tickets,
                place=place,
                estimate=estimates.get(lease.id),
            )
            for place, lease in enumerate(waiting, start=1)
        ],
        "head_shortfall": head_shortfall,
        "orphaned": [lease.id for lease in holders if lease.status is DockerLeaseStatus.ORPHANED],
        "unverifiable": [
            {
                "lease_id": lease.id,
                "outcome": lease.last_probe_outcome,
                "error": lease.last_probe_error,
            }
            for lease in unverifiable
        ],
    }


def _pool_summary(pool: DockerCapacityPool, ceiling: Ceiling) -> dict:
    return {
        "ceiling": ceiling.as_dict(),
        "in_use": {
            "cpus": pool.held_cpus,
            "memory_mb": pool.held_memory_mb,
            "leases": pool.held_count,
        },
        "available": {
            "cpus": max(0.0, round(ceiling.cpus - pool.held_cpus, 2)),
            "memory_mb": max(0, ceiling.memory_mb - pool.held_memory_mb),
            "leases": max(0, ceiling.leases - pool.held_count),
        },
    }
