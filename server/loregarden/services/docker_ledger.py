"""Primitives every layer of the docker capacity ledger needs.

Extracted to break a real cycle rather than to dodge one: `docker_leases` needs
`docker_wait_estimate` to tell a queued caller when it will start, and the
estimator needs the pool and the lease vocabulary to work that out. Both sit
above this module, and neither imports the other's internals.

Nothing here decides anything. It is the shared reading of what a lease *is* —
which statuses occupy capacity, where the pool row lives, and how to read a
timestamp back out of SQLite.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime

from loregarden.core.timestamps import as_utc as _as_utc_aware
from loregarden.models.domain import (
    CapacityPool,
    CapacityResource,
    DockerCapacityPool,
    DockerLease,
    DockerLeaseStatus,
)
from loregarden.models.domain.docker_tables import POOL_ROW_IDS
from loregarden.services.docker_capacity import Ceiling
from pydantic import TypeAdapter, ValidationError
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session

logger = logging.getLogger(__name__)

#: Statuses that count against the ceiling. `ORPHANED` is included on purpose:
#: its containers are confirmed to still be running, so the machine really is
#: that busy, and freeing it would over-book a box that is genuinely loaded.
OCCUPYING = (DockerLeaseStatus.HELD, DockerLeaseStatus.ORPHANED)

#: The pools a claim is booked against. Docker containers run on the host, so a
#: docker claim spends both; a host claim (a test suite) spends the host alone.
CHARGED_POOLS: dict[CapacityPool, tuple[CapacityPool, ...]] = {
    CapacityPool.DOCKER: (CapacityPool.DOCKER, CapacityPool.HOST),
    CapacityPool.HOST: (CapacityPool.HOST,),
}

#: The inverse: which lease pools each pool is charged by.
CHARGED_BY: dict[CapacityPool, tuple[CapacityPool, ...]] = {
    pool: tuple(claim for claim, charged in CHARGED_POOLS.items() if pool in charged)
    for pool in CapacityPool
}

#: The stored `container_names_json` payload, validated rather than
#: hand-inspected. It is written by this process, but it is still a blob coming
#: back out of a text column, and `isinstance(parsed, list)` is a schema check
#: written by hand — the thing the organization gate exists to stop.
_CONTAINER_NAMES = TypeAdapter(list[str])


@dataclass(frozen=True)
class Booking:
    """What a lease is charged against each pool it touches."""

    cpus: float
    memory_mb: int
    count: int


def booked(lease: DockerLease) -> Booking:
    """A lease's price less what its parent's grant covered.

    A top-level lease books its whole price. A child books only the excess over
    its parent's unused allotment. Either takes a lease-count slot only when
    `takes_slot` says so — a child runs inside its parent's slot, and an agent
    run's standing claim takes none.
    """
    return Booking(
        cpus=lease.cpus - lease.covered_cpus,
        memory_mb=lease.memory_mb - lease.covered_memory_mb,
        count=1 if lease.takes_slot else 0,
    )


@dataclass(frozen=True)
class Shortfall:
    """One way a claim does not fit in one pool: what it needs, and what is free."""

    pool: CapacityPool
    resource: CapacityResource
    needed: float
    free: float

    def as_dict(self) -> dict:
        return {
            "pool": self.pool.value,
            "resource": self.resource.value,
            "needed": self.needed,
            "free": self.free,
        }


def shortfalls(name: CapacityPool, pool: DockerCapacityPool, lease: DockerLease) -> list[Shortfall]:
    """Every dimension a top-level `lease` does not fit in `pool`; empty when it fits.

    Admission's own test, so the board's "waiting for 4 cpus, 3 free" is the
    reason the drain is holding the claim back, not a second opinion of it.
    """
    checks = (
        (CapacityResource.CPUS, lease.cpus, pool.held_cpus, pool.ceiling_cpus),
        (CapacityResource.MEMORY_MB, lease.memory_mb, pool.held_memory_mb, pool.ceiling_memory_mb),
        (CapacityResource.SLOTS, booked(lease).count, pool.held_count, pool.ceiling_leases),
    )
    return [
        Shortfall(pool=name, resource=resource, needed=needed, free=max(0, ceiling - held))
        for resource, needed, held, ceiling in checks
        if held + needed > ceiling
    ]


def as_utc(stamp: datetime | None) -> datetime | None:
    """`core.timestamps.as_utc`, widened to accept a column that may be NULL.

    Every nullable timestamp on a lease — `granted_at`, `expires_at`,
    `released_at` — is read through here. SQLite hands back naive datetimes
    whatever went in, so comparing one against `datetime.now(timezone.utc)`
    raises rather than answering, and it would raise inside the reaper at
    exactly the moment the reaper is the thing keeping the pool honest.
    """
    return None if stamp is None else _as_utc_aware(stamp)


def last_seen_at(lease: DockerLease) -> datetime | None:
    """The last time a waiter showed it is still there: a served poll, a
    renewal, or the request itself.

    The abandonment sweep drops a waiter by this, and the board flags a stalled
    one by it, so the two read one clock and cannot disagree about who is gone.
    """
    seen = [
        stamp
        for stamp in (
            as_utc(lease.last_polled_at),
            as_utc(lease.last_renewed_at),
            as_utc(lease.requested_at),
        )
        if stamp is not None
    ]
    return max(seen) if seen else None


def container_names(lease: DockerLease) -> list[str]:
    """The container names a lease recorded, or none if the column is unreadable.

    Unreadable is logged, not swallowed: a lease whose names cannot be parsed is
    reaped on its clock as though it had bound nothing, and that is a decision
    somebody should be able to find afterwards.
    """
    try:
        return _CONTAINER_NAMES.validate_json(lease.container_names_json or "[]")
    except ValidationError:
        logger.warning(
            "Lease %s has an unreadable container_names_json (%r); treating it as "
            "naming no containers, which means it will be reaped on its TTL alone",
            lease.id,
            lease.container_names_json,
        )
        return []


def load_pool(session: Session, pool: CapacityPool = CapacityPool.DOCKER) -> DockerCapacityPool:
    """The pool's row, created on first use if the migration has not run yet.

    Lazy creation here is safe in a way it was NOT for `agent_slots`, and the
    difference is worth stating because the surface reads identically. That pool
    was keyed by `slot_number` with no unique constraint, so two threads
    initialising it each inserted a full set and the machine ran six agents
    against a limit of three. This row's identity is a constant primary key: two
    racers both inserting the same id means one insert and one integrity error,
    never two pools.

    The migration still seeds it, so a migrated database never reaches the
    fallback. It exists for the schema paths that skip migrations — `create_all`
    on a fresh database, and every test engine.
    """
    row_id = POOL_ROW_IDS[pool]
    row = session.get(DockerCapacityPool, row_id)
    if row is not None:
        return row

    session.add(DockerCapacityPool(id=row_id))
    try:
        session.commit()
    except IntegrityError:
        # The other half of "one insert and one integrity error": a racer
        # created the row first. Its row is the pool; read that one.
        session.rollback()
    row = session.get(DockerCapacityPool, row_id)
    if row is None:  # pragma: no cover — the insert above either lands or raises
        raise RuntimeError(f"could not create the docker_capacity_pool row {row_id!r}")
    return row


def pool_ceiling(pool: DockerCapacityPool) -> Ceiling:
    return Ceiling(
        cpus=pool.ceiling_cpus,
        memory_mb=pool.ceiling_memory_mb,
        leases=pool.ceiling_leases,
        source=pool.ceiling_source,
        probed_at=pool.probed_at,
        error=pool.probe_error,
    )
