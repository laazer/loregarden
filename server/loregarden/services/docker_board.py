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

from loregarden.models.domain import DockerLease, DockerLeaseStatus, DockerProbeOutcome
from loregarden.services.docker_capacity import DockerInvoke
from loregarden.services.docker_leases import (
    OCCUPYING,
    as_utc,
    container_names,
    load_pool,
    pool_ceiling,
    refresh_ceiling,
)
from loregarden.services.docker_subprocess import run_docker
from sqlmodel import Session, select


def _lease_payload(lease: DockerLease, *, now: datetime) -> dict:
    expires_at = as_utc(lease.expires_at)
    return {
        "lease_id": lease.id,
        "status": lease.status.value,
        "holder_label": lease.holder_label,
        "holder_kind": lease.holder_kind.value,
        "agent_run_id": lease.agent_run_id,
        "ticket_id": lease.ticket_id,
        "footprint": lease.footprint.value,
        "cpus": lease.cpus,
        "memory_mb": lease.memory_mb,
        "compose_project": lease.compose_project,
        "container_names": container_names(lease),
        "position": lease.position or None,
        "expires_at": expires_at.isoformat() if expires_at else None,
        "expires_in_seconds": (int((expires_at - now).total_seconds()) if expires_at else None),
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

    return {
        "enabled": True,
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
        "holders": [_lease_payload(lease, now=stamp) for lease in holders],
        "waiting": [_lease_payload(lease, now=stamp) for lease in waiting],
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
