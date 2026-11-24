"""Which waiters may start ahead of a head that does not fit, and when.

The line is first come, first served, so a 4-cpu suite at its head held back
everything behind it, including an agent run wanting a quarter of a cpu that was
free the whole time. Over 2026-11-09..23, 32 of 309 agent runs waited more than
10 minutes for 0.25 cpus, the longest 111 minutes.

**Who may pass.** A *small standing claim*: an agent run's host lease, or the
host lease its orchestration holds between gates. Neither takes a slot, because
neither is work the slots space out, and both are under
`host_load_gate_min_cpus`. Everything else keeps its place.

**When.** Passing must never make the head wait. The obvious way to promise that
is to prove a passing claim ends before the head starts, and nothing here can:
a lease is renewed for as long as its holder lives (lg-machine-resource-1925
stalled on this). So time is not used at all. A claim may pass only if, for
**every way the running work could finish**, the head still fits beside all
standing claims and this one whenever it would fit beside none. The head then
starts on the same release it would have started on. That also bounds how much
can pass: on 7 cpus with a 4-cpu suite running and another at the head, 3 cpus.

This is EASY backfill's second rule (a job may use only what the head's
reservation leaves over), judged on the worst case rather than on predicted
finish times. Like EASY it protects the head, not every waiter: a claim further
back may find standing claims in its way when it reaches the front. From then on
nothing more may pass it, so it waits at most for the ones already running.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from loregarden.config import settings
from loregarden.models.domain import (
    CapacityPool,
    DockerCapacityPool,
    DockerHolderKind,
    DockerLease,
    DockerLeaseStatus,
)
from loregarden.services.docker_ledger import CHARGED_BY, OCCUPYING, booked
from sqlmodel import Session, col, select

#: Claims held for as long as an agent or an orchestration lives, rather than for
#: a piece of work that finishes.
STANDING_KINDS = (DockerHolderKind.AGENT_RUN, DockerHolderKind.ORCHESTRATION)

#: Cpu totals are float sums, so an exact fit can read as a hair over.
_CPU_SLACK = 1e-9


@dataclass(frozen=True)
class Room:
    """An amount of one pool: what is free, held, or wanted."""

    cpus: float = 0.0
    memory_mb: int = 0

    def __add__(self, other: Room) -> Room:
        return Room(self.cpus + other.cpus, self.memory_mb + other.memory_mb)

    def __sub__(self, other: Room) -> Room:
        return Room(self.cpus - other.cpus, self.memory_mb - other.memory_mb)

    def fits(self, ceiling: Room) -> bool:
        return self.cpus <= ceiling.cpus + _CPU_SLACK and self.memory_mb <= ceiling.memory_mb


def is_standing(lease: DockerLease) -> bool:
    """Whether this claim is held for a run's lifetime rather than for a piece of work.

    A stage's docker stack belongs to an agent run too, but it takes a slot: it
    is work, and it finishes.
    """
    return lease.holder_kind in STANDING_KINDS and not lease.takes_slot


def may_pass_head(lease: DockerLease) -> bool:
    """Whether this waiter is of the kind that may start ahead of a blocked head."""
    return (
        is_standing(lease)
        and lease.pool is CapacityPool.HOST
        and lease.cpus < settings.host_load_gate_min_cpus
    )


def passing_leaves_head_alone(
    *, ceiling: Room, held: Room, work: Iterable[Room], head: Room, claim: Room
) -> bool:
    """Whether granting `claim` now leaves the head's start exactly where it was.

    `held` is the pool's running total and `work` the part of it that finishes.
    The difference is what stands, which errs towards refusing: capacity booked
    for a claim whose row is not HELD yet counts as standing for that instant.
    """
    work = list(work)
    standing = held - sum(work, Room())
    # The distinct totals still held, over every subset of the work that could be
    # left running. Equal claims collapse, so this is a handful of entries.
    remaining = {Room()}
    for piece in work:
        remaining |= {left + piece for left in remaining}
    return all(
        (left + head + standing + claim).fits(ceiling)
        for left in remaining
        if (left + head).fits(ceiling)
    )


def host_work(session: Session) -> list[Room]:
    """What each piece of running work has booked against the host pool."""
    rows = session.exec(
        select(DockerLease).where(
            col(DockerLease.status).in_(OCCUPYING),
            col(DockerLease.pool).in_(CHARGED_BY[CapacityPool.HOST]),
        )
    ).all()
    bookings = [booked(row) for row in rows if not is_standing(row)]
    return [Room(b.cpus, b.memory_mb) for b in bookings if b.cpus > 0 or b.memory_mb > 0]


def may_pass_now(
    session: Session, pool: DockerCapacityPool, *, head: DockerLease, claim: DockerLease
) -> bool:
    """`passing_leaves_head_alone` on the host pool as `pool` reads now.

    Only the host pool: the claims that pass are charged to nothing else, and
    every head is charged to it.
    """
    return passing_leaves_head_alone(
        ceiling=Room(pool.ceiling_cpus, pool.ceiling_memory_mb),
        held=Room(pool.held_cpus, pool.held_memory_mb),
        work=host_work(session),
        head=Room(head.cpus, head.memory_mb),
        claim=Room(claim.cpus, claim.memory_mb),
    )


def next_to_pass(session: Session, head: DockerLease) -> DockerLease | None:
    """The first waiter behind `head` that is of the kind that may pass it.

    Only the first: passing claims keep their own order, so a 0.25-cpu run
    cannot starve a 0.5-cpu orchestration either.
    """
    behind = session.exec(
        select(DockerLease)
        .where(
            DockerLease.status == DockerLeaseStatus.WAITING,
            DockerLease.position > head.position,
            col(DockerLease.holder_kind).in_(STANDING_KINDS),
        )
        .order_by(col(DockerLease.position))
    ).all()
    return next((lease for lease in behind if may_pass_head(lease)), None)
