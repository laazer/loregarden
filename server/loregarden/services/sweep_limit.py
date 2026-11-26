"""At most one sweep per repository holds capacity at a time.

A timing or mutation sweep is heavy, optional and run many times over. On
2026-11-25 several sessions each ran one beside the pushes and held load at
60-77 on 10 cores, so every suite on the box ran slow. The footprint alone did
not stop it: each sweep fit on its own, and together they filled the machine
the pushes needed.

So the granter treats a sweep as blocked while another sweep from the same
repository holds capacity, or waits ahead of it. A blocked sweep keeps its place
but is *skipped*, not waited behind: the rule is about sweeps, and a push or a
landing queued behind a blocked sweep must start as soon as it fits. That is
the one exception to head-of-line, and it cannot starve anything — the sweep it
passes is waiting on another sweep, not on room.

Sweeps of one repository are granted in the order they asked, and never two at
once, without a lock. The waiters are read before the holders, and a lease
leaves the line and becomes a holder in one statement, so a sweep that left the
first read is in the second. The walk returns the earliest sweep of a repository
before it reaches a later one, so two drains racing aim at the same lease, and
the promotion's compare-and-set lets one of them have it.
"""

from __future__ import annotations

from collections.abc import Iterable

from loregarden.models.domain import CapacityClass, DockerLease, DockerLeaseStatus
from loregarden.services.docker_ledger import OCCUPYING
from sqlmodel import Session, col, select


def is_sweep(lease: DockerLease) -> bool:
    return lease.capacity_class is CapacityClass.SWEEP


def _sweeps_holding(session: Session) -> set[str]:
    """The repositories whose sweep slot is taken now."""
    return set(
        session.exec(
            select(DockerLease.repo_key).where(
                col(DockerLease.status).in_(OCCUPYING),
                DockerLease.capacity_class == CapacityClass.SWEEP,
            )
        ).all()
    )


def first_grantable(session: Session, waiters: Iterable[DockerLease]) -> DockerLease | None:
    """The first of `waiters` (in line order) that the sweep rule does not hold back."""
    holding: set[str] | None = None
    for lease in waiters:
        if not is_sweep(lease):
            return lease
        if holding is None:
            holding = _sweeps_holding(session)  # read only once a sweep is in the way
        if lease.repo_key not in holding:
            return lease
    return None


def sweep_holder(session: Session, lease: DockerLease) -> DockerLease | None:
    """The sweep this one waits behind: the one holding its repository's slot, else
    the earliest of its repository still ahead of it in line. None when it waits on
    nothing but room, or is not a sweep.
    """
    if not is_sweep(lease):
        return None
    same_repo = select(DockerLease).where(
        DockerLease.capacity_class == CapacityClass.SWEEP,
        DockerLease.repo_key == lease.repo_key,
        DockerLease.id != lease.id,
    )
    holding = session.exec(same_repo.where(col(DockerLease.status).in_(OCCUPYING))).first()
    if holding is not None:
        return holding
    return session.exec(
        same_repo.where(
            DockerLease.status == DockerLeaseStatus.WAITING,
            col(DockerLease.position) < lease.position,
        ).order_by(col(DockerLease.position))
    ).first()
