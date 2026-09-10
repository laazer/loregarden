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
from datetime import datetime

from loregarden.core.timestamps import as_utc as _as_utc_aware
from loregarden.models.domain import DockerCapacityPool, DockerLease, DockerLeaseStatus
from loregarden.models.domain.docker_tables import GLOBAL_POOL_ID
from loregarden.services.docker_capacity import Ceiling
from pydantic import TypeAdapter, ValidationError
from sqlmodel import Session

logger = logging.getLogger(__name__)

#: Statuses that count against the ceiling. `ORPHANED` is included on purpose:
#: its containers are confirmed to still be running, so the machine really is
#: that busy, and freeing it would over-book a box that is genuinely loaded.
OCCUPYING = (DockerLeaseStatus.HELD, DockerLeaseStatus.ORPHANED)

#: The stored `container_names_json` payload, validated rather than
#: hand-inspected. It is written by this process, but it is still a blob coming
#: back out of a text column, and `isinstance(parsed, list)` is a schema check
#: written by hand — the thing the organization gate exists to stop.
_CONTAINER_NAMES = TypeAdapter(list[str])


def as_utc(stamp: datetime | None) -> datetime | None:
    """`core.timestamps.as_utc`, widened to accept a column that may be NULL.

    Every nullable timestamp on a lease — `granted_at`, `expires_at`,
    `released_at` — is read through here. SQLite hands back naive datetimes
    whatever went in, so comparing one against `datetime.now(timezone.utc)`
    raises rather than answering, and it would raise inside the reaper at
    exactly the moment the reaper is the thing keeping the pool honest.
    """
    return None if stamp is None else _as_utc_aware(stamp)


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


def load_pool(session: Session) -> DockerCapacityPool:
    """The singleton, created on first use if the migration has not run yet.

    Lazy creation here is safe in a way it was NOT for `agent_slots`, and the
    difference is worth stating because the surface reads identically. That pool
    was keyed by `slot_number` with no unique constraint, so two threads
    initialising it each inserted a full set and the machine ran six agents
    against a limit of three. This row's identity is a constant primary key: two
    racers both inserting `'global'` means one insert and one integrity error,
    never two pools.

    The migration still seeds it, so a migrated database never reaches the
    fallback. It exists for the schema paths that skip migrations — `create_all`
    on a fresh database, and every test engine.
    """
    pool = session.get(DockerCapacityPool, GLOBAL_POOL_ID)
    if pool is not None:
        return pool

    session.add(DockerCapacityPool(id=GLOBAL_POOL_ID))
    session.commit()
    pool = session.get(DockerCapacityPool, GLOBAL_POOL_ID)
    if pool is None:  # pragma: no cover — the insert above either lands or raises
        raise RuntimeError("could not create the docker_capacity_pool singleton")
    return pool


def pool_ceiling(pool: DockerCapacityPool) -> Ceiling:
    return Ceiling(
        cpus=pool.ceiling_cpus,
        memory_mb=pool.ceiling_memory_mb,
        leases=pool.ceiling_leases,
        source=pool.ceiling_source,
        probed_at=pool.probed_at,
        error=pool.probe_error,
    )
