"""Child leases: a claim made by something that already holds capacity.

A gate run inside an agent run, or a nested `capacity run` inside a pre-push,
asks for capacity while its parent is holding some. Put through the ordinary
queue, that deadlocks: two agents each hold half the machine, each gate waits
for room only the other agent's finish would free, and neither agent finishes
until its gate runs. Strict head-of-line makes it worse, not better.

So a child **never waits**:

1. Its price is paid first from the parent's unused allotment — what the parent
   was granted and has not already lent to its other live children.
2. Any excess is booked against the pools *now*, ahead of the queue, because
   the parent's work is in progress and the capacity it holds is idle until
   the child runs.
3. If the excess does not fit, the child is refused at once
   (`exceeds_parent_grant`). The fix is a parent sized for its children.

No child ever waits, so no holder ever waits, and every wait in the system is
for a holder that can finish: there is no cycle to deadlock on.

Covering a child is the same shape as booking a pool: one conditional UPDATE
on the parent's `child_covered_*` totals, compared against the value read, in
the same transaction as any excess booking and the child's insert. A lost race
retries; nothing commits half a grant.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta, timezone

from loregarden.config import settings
from loregarden.models.domain import (
    CapacityPool,
    DockerFootprint,
    DockerGrantState,
    DockerHolderKind,
    DockerLease,
    DockerLeaseStatus,
)
from loregarden.models.domain.docker_tables import POOL_ROW_IDS
from loregarden.services.docker_capacity import DockerInvoke, resolve_weights
from loregarden.services.docker_leases import (
    CLAIM_CONTENTION_BUDGET_SECONDS,
    REJECT_DISABLED,
    REJECT_EXCEEDS_PARENT_GRANT,
    REJECT_PARENT_NOT_HELD,
    REJECT_PARENT_POOL_MISMATCH,
    REJECT_UNKNOWN_LEASE,
    DockerReservation,
    ceiling_refusal,
    claim_pool_capacity,
)
from loregarden.services.docker_ledger import CHARGED_POOLS, OCCUPYING, as_utc, load_pool
from loregarden.services.docker_subprocess import run_docker
from sqlmodel import Session, update

logger = logging.getLogger(__name__)


def reserve_child(
    session: Session,
    *,
    parent_lease_id: str,
    holder_label: str,
    footprint: DockerFootprint = DockerFootprint.CUSTOM,
    cpus: float = 0.0,
    memory_mb: int = 0,
    pool: CapacityPool | None = None,
    ttl_seconds: int | None = None,
    holder_kind: DockerHolderKind = DockerHolderKind.AD_HOC,
    agent_run_id: str | None = None,
    orchestration_run_id: str | None = None,
    ticket_id: str | None = None,
    workspace_id: str | None = None,
    holder_pid: int | None = None,
    invoke: DockerInvoke = run_docker,
) -> DockerReservation:
    """Grant a child of `parent_lease_id` now, or refuse it now. Never queues.

    `pool` defaults to the parent's. A child may only claim pools its parent is
    charged to — a host child of a docker parent, not the reverse — because the
    parent's allotment only exists in those pools.
    """
    if not settings.docker_capacity_enabled:
        return DockerReservation(
            state=DockerGrantState.REJECTED,
            error_kind=REJECT_DISABLED,
            message="The capacity ledger is disabled.",
        )
    parent = session.get(DockerLease, parent_lease_id)
    if parent is None:
        return DockerReservation(
            state=DockerGrantState.REJECTED,
            error_kind=REJECT_UNKNOWN_LEASE,
            message=f"No lease {parent_lease_id} to nest under.",
        )
    if parent.status not in OCCUPYING:
        return DockerReservation(
            state=DockerGrantState.REJECTED,
            error_kind=REJECT_PARENT_NOT_HELD,
            message=f"Parent lease {parent_lease_id} is {parent.status.value}, not held.",
        )
    child_pool = pool or parent.pool
    if not set(CHARGED_POOLS[child_pool]) <= set(CHARGED_POOLS[parent.pool]):
        return DockerReservation(
            state=DockerGrantState.REJECTED,
            error_kind=REJECT_PARENT_POOL_MISMATCH,
            message=f"A {child_pool.value} claim cannot draw on a {parent.pool.value} parent: "
            f"the parent holds nothing in the {child_pool.value} pool.",
        )

    price_cpus, price_memory = resolve_weights(footprint, cpus=cpus, memory_mb=memory_mb)
    for charged in CHARGED_POOLS[child_pool]:
        refusal = ceiling_refusal(
            session, charged, cpus=price_cpus, memory_mb=price_memory, invoke=invoke
        )
        if refusal is not None:
            return refusal

    child = DockerLease(
        status=DockerLeaseStatus.HELD,
        holder_kind=holder_kind,
        holder_label=holder_label,
        parent_lease_id=parent_lease_id,
        agent_run_id=agent_run_id,
        orchestration_run_id=orchestration_run_id,
        ticket_id=ticket_id,
        workspace_id=workspace_id,
        holder_pid=holder_pid,
        pool=child_pool,
        footprint=footprint,
        cpus=price_cpus,
        memory_mb=price_memory,
        position=0,
        ttl_seconds=_ttl(ttl_seconds),
    )
    return _grant(session, child)


def _ttl(ttl_seconds: int | None) -> int:
    requested = settings.docker_lease_ttl_seconds if ttl_seconds is None else ttl_seconds
    return max(1, min(int(requested), settings.docker_lease_max_ttl_seconds))


def _grant(session: Session, child: DockerLease) -> DockerReservation:
    """Cover, book the excess, and insert — together, or not at all."""
    pools = CHARGED_POOLS[child.pool]
    deadline = time.monotonic() + CLAIM_CONTENTION_BUDGET_SECONDS
    while True:
        session.expire_all()
        parent = session.get(DockerLease, child.parent_lease_id)
        if parent is None or parent.status not in OCCUPYING:
            return DockerReservation(
                state=DockerGrantState.REJECTED,
                error_kind=REJECT_PARENT_NOT_HELD,
                message="The parent lease ended before the grant.",
            )
        child.covered_cpus = min(child.cpus, max(0.0, parent.cpus - parent.child_covered_cpus))
        child.covered_memory_mb = min(
            child.memory_mb, max(0, parent.memory_mb - parent.child_covered_memory_mb)
        )
        excess_cpus = child.cpus - child.covered_cpus
        excess_memory = child.memory_mb - child.covered_memory_mb
        needs_pools = excess_cpus > 0 or excess_memory > 0
        # Every row read before the first UPDATE: a lazily created pool commits.
        revisions = {name: load_pool(session, name).revision for name in pools}

        if not _cover(session, parent, child):
            session.rollback()
            if _out_of_time(deadline, child):
                return _contended(child)
            continue
        if needs_pools and not all(
            claim_pool_capacity(
                session,
                pool_id=POOL_ROW_IDS[name],
                cpus=excess_cpus,
                memory_mb=excess_memory,
                revision=revisions[name],
                count=0,
            )
            for name in pools
        ):
            session.rollback()
            if not _excess_fits(session, pools, excess_cpus, excess_memory):
                return DockerReservation(
                    state=DockerGrantState.REJECTED,
                    error_kind=REJECT_EXCEEDS_PARENT_GRANT,
                    message=f"{child.cpus:g} cpus / {child.memory_mb} MB is "
                    f"{excess_cpus:g} cpus / {excess_memory} MB more than parent "
                    f"{parent.id} has unlent, and that excess does not fit right now. "
                    "A child never waits in line; size the parent to cover its children.",
                )
            if _out_of_time(deadline, child):
                return _contended(child)
            continue

        now = datetime.now(timezone.utc)
        child.granted_at = now
        child.last_renewed_at = now
        child.expires_at = now + timedelta(seconds=child.ttl_seconds)
        session.add(child)
        session.commit()
        return DockerReservation(
            state=DockerGrantState.GRANTED,
            lease_id=child.id,
            cpus=child.cpus,
            memory_mb=child.memory_mb,
            expires_at=as_utc(child.expires_at),
            message=(
                f"Granted {child.cpus:g} cpus / {child.memory_mb} MB under {parent.id}: "
                f"{child.covered_cpus:g} cpus / {child.covered_memory_mb} MB from the parent, "
                f"{excess_cpus:g} cpus / {excess_memory} MB from the pools."
            ),
            _session=session,
        )


def _cover(session: Session, parent: DockerLease, child: DockerLease) -> bool:
    """Draw the child's cover from the parent, if the parent's totals are as read."""
    result = session.exec(
        update(DockerLease)
        .where(DockerLease.id == parent.id)
        .where(DockerLease.status.in_(OCCUPYING))
        .where(DockerLease.child_covered_cpus == parent.child_covered_cpus)
        .where(DockerLease.child_covered_memory_mb == parent.child_covered_memory_mb)
        .values(
            child_covered_cpus=DockerLease.child_covered_cpus + child.covered_cpus,
            child_covered_memory_mb=DockerLease.child_covered_memory_mb + child.covered_memory_mb,
        )
        .execution_options(synchronize_session=False)
    )
    return bool(result.rowcount == 1)


def _excess_fits(
    session: Session, pools: tuple[CapacityPool, ...], cpus: float, memory_mb: int
) -> bool:
    session.expire_all()
    for name in pools:
        row = load_pool(session, name)
        if row.held_cpus + cpus > row.ceiling_cpus:
            return False
        if row.held_memory_mb + memory_mb > row.ceiling_memory_mb:
            return False
    return True


def _out_of_time(deadline: float, child: DockerLease) -> bool:
    if time.monotonic() < deadline:
        return False
    logger.warning(
        "Child lease for %s lost every race for %.0fs while it fit; refusing rather than spinning",
        child.holder_label,
        CLAIM_CONTENTION_BUDGET_SECONDS,
    )
    return True


def _contended(child: DockerLease) -> DockerReservation:
    return DockerReservation(
        state=DockerGrantState.REJECTED,
        error_kind=REJECT_EXCEEDS_PARENT_GRANT,
        message=f"Could not grant {child.holder_label} under contention within "
        f"{CLAIM_CONTENTION_BUDGET_SECONDS:.0f}s; it fit, and retrying may succeed.",
    )
