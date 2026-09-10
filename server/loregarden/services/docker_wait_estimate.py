"""How long a queued docker claim will actually wait.

Same rule `run_duration_stats` sets and for the same reason: the denominator is
what leases have actually cost on this machine, and when there is no history the
answer is ``None``. A constant default would put the fabrication back — and an
invented wait is worse here than a missing one, because the number is what the
caller uses to decide when to poll.

**Why a simulation rather than arithmetic.** The agent queue can divide a
position by a lane count because every lane holds exactly one occupant. Capacity
is two-dimensional and weighted: three small leases can free enough memory for a
large waiter while freeing no cpus, and a waiter behind a large one can be
unblocked by a release that would not have unblocked the large one. The only
honest answer walks the queue the way the drain does — head-of-line, releasing
holders in the order they are predicted to finish — and reports where this
waiter comes out.

**What predicts a holder's release.** Its footprint's median observed hold when
there is one, because that reflects what these leases really do, renewals
included. Otherwise its remaining TTL, which is a bound rather than an estimate
and is marked as such by producing a coarser answer. A holder with neither makes
every waiter behind it unknown, rather than being skipped — skipping it would
quietly predict a queue that cannot move.

**Only clean releases count as history.** A lease reclaimed at `TTL_EXPIRED` or
`ABANDONED` records the moment the reaper noticed, not the moment the work
finished, so folding those in would teach the estimator that every claim lasts
exactly one TTL.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from statistics import median

from loregarden.config import settings
from loregarden.models.domain import (
    DockerCapacityPool,
    DockerFootprint,
    DockerLease,
    DockerLeaseEndReason,
    DockerLeaseStatus,
    DockerWaitBasis,
)
from loregarden.services.docker_ledger import OCCUPYING, as_utc, load_pool
from sqlmodel import Session, col, select

#: End reasons whose `released_at` marks the work finishing rather than the
#: reaper noticing it had not. Only these teach the estimator anything.
CLEAN_RELEASES = (DockerLeaseEndReason.RELEASED, DockerLeaseEndReason.RUN_COMPLETED)


@dataclass(frozen=True)
class HoldStats:
    """Median seconds a lease is actually held, by footprint and overall."""

    by_footprint: dict[DockerFootprint, float] = field(default_factory=dict)
    overall: float | None = None
    samples: int = 0

    def predict(self, footprint: DockerFootprint) -> float | None:
        """The best available median for this footprint, or None.

        Falls back to the overall median rather than to a constant: a machine
        with history for `stack` and none for `heavy` knows more than nothing
        about `heavy`, and less than it does about `stack`.
        """
        specific = self.by_footprint.get(footprint)
        if specific is not None:
            return specific
        return self.overall


def observed_hold_times(session: Session, *, now: datetime | None = None) -> HoldStats:
    """Median hold time per footprint, from leases their holders actually released."""
    stamp = now or datetime.now(timezone.utc)
    cutoff = stamp - timedelta(days=settings.docker_wait_lookback_days)

    rows = session.exec(
        select(DockerLease).where(
            DockerLease.status == DockerLeaseStatus.RELEASED,
            col(DockerLease.end_reason).in_(CLEAN_RELEASES),
            col(DockerLease.granted_at).is_not(None),
            col(DockerLease.released_at).is_not(None),
        )
    ).all()

    per_footprint: dict[DockerFootprint, list[float]] = {}
    everything: list[float] = []
    for lease in rows:
        granted = as_utc(lease.granted_at)
        released = as_utc(lease.released_at)
        if granted is None or released is None or released < cutoff:
            continue
        held = (released - granted).total_seconds()
        if held <= 0:
            continue
        per_footprint.setdefault(lease.footprint, []).append(held)
        everything.append(held)

    return HoldStats(
        by_footprint={fp: median(values) for fp, values in per_footprint.items()},
        overall=median(everything) if everything else None,
        samples=len(everything),
    )


@dataclass(frozen=True)
class WaitEstimate:
    """How long a waiter will wait, and how much that number is worth.

    `seconds` alone cannot express the difference between "six minutes, measured"
    and "at most fifteen, because that is the TTL nobody has beaten yet", and the
    two call for different polling. `basis` says which, and `UNKNOWN` keeps
    `seconds` at None rather than letting a stand-in constant in.
    """

    seconds: int | None
    basis: DockerWaitBasis

    def as_dict(self) -> dict:
        return {"estimated_wait_seconds": self.seconds, "estimate_basis": self.basis.value}


UNKNOWN_WAIT = WaitEstimate(seconds=None, basis=DockerWaitBasis.UNKNOWN)


@dataclass
class _Holder:
    """A lease occupying capacity, and when it is expected to give it back."""

    cpus: float
    memory_mb: int
    #: Seconds from now. None when nothing can predict it — which propagates
    #: rather than being skipped.
    releases_in: float | None
    #: Whether `releases_in` came from measured history or from a TTL. A waiter
    #: is only as well-predicted as the weakest holder it had to wait for.
    basis: DockerWaitBasis = DockerWaitBasis.UNKNOWN


def _holders(session: Session, stats: HoldStats, *, now: datetime) -> list[_Holder]:
    leases = session.exec(select(DockerLease).where(col(DockerLease.status).in_(OCCUPYING))).all()

    holders: list[_Holder] = []
    for lease in leases:
        predicted = stats.predict(lease.footprint)
        granted = as_utc(lease.granted_at)
        releases_in: float | None = None
        basis = DockerWaitBasis.UNKNOWN
        if predicted is not None and granted is not None:
            releases_in = max(0.0, (granted + timedelta(seconds=predicted) - now).total_seconds())
            basis = DockerWaitBasis.HISTORY
        else:
            expires = as_utc(lease.expires_at)
            if expires is not None:
                releases_in = max(0.0, (expires - now).total_seconds())
                basis = DockerWaitBasis.TTL_BOUND
        holders.append(
            _Holder(
                cpus=lease.cpus,
                memory_mb=lease.memory_mb,
                releases_in=releases_in,
                basis=basis,
            )
        )
    return holders


def estimate_waits(
    session: Session,
    *,
    now: datetime | None = None,
    stats: HoldStats | None = None,
) -> dict[str, WaitEstimate]:
    """Seconds until each waiting lease is expected to start, keyed by lease id.

    ``None`` for any waiter whose answer depends on a holder nothing can predict.
    Head-of-line throughout, matching `drain_waiters`: a waiter is never
    projected to start before the one ahead of it, even when it would fit
    sooner, because the drain will not grant it sooner.
    """
    stamp = now or datetime.now(timezone.utc)
    hold_stats = stats if stats is not None else observed_hold_times(session, now=stamp)
    pool: DockerCapacityPool = load_pool(session)

    free_cpus = pool.ceiling_cpus - pool.held_cpus
    free_memory = pool.ceiling_memory_mb - pool.held_memory_mb
    free_leases = pool.ceiling_leases - pool.held_count

    holders = _holders(session, hold_stats, now=stamp)
    waiters = session.exec(
        select(DockerLease)
        .where(DockerLease.status == DockerLeaseStatus.WAITING)
        .order_by(col(DockerLease.position))
    ).all()

    estimates: dict[str, WaitEstimate] = {}
    clock = 0.0
    #: Degrades to TTL_BOUND the moment any holder we wait on was predicted from
    #: a TTL. A chain is only as strong as its weakest link, and presenting a
    #: bound as a forecast is the failure this whole field exists to prevent.
    basis = DockerWaitBasis.HISTORY
    #: Once a holder with no prediction has to be waited on, nothing behind it
    #: can be estimated either. Sticky rather than per-waiter: the queue is
    #: ordered, so an unpredictable blocker blocks everything after it.
    blind = False

    for waiter in waiters:
        while not blind and not _fits(waiter, free_cpus, free_memory, free_leases):
            pending = [h for h in holders if h.releases_in is not None]
            if not pending:
                # Either nothing is holding (so the ceiling itself is too small,
                # which admission refuses rather than queues) or every holder is
                # unpredictable. Both mean there is no honest number.
                blind = True
                break
            soonest = min(pending, key=lambda h: h.releases_in or 0.0)
            if any(h.releases_in is None for h in holders):
                # An unpredictable holder might free capacity sooner than the
                # one we are about to wait for, so this estimate would be an
                # upper bound presented as a prediction.
                blind = True
                break
            clock = max(clock, soonest.releases_in or 0.0)
            if soonest.basis is DockerWaitBasis.TTL_BOUND:
                basis = DockerWaitBasis.TTL_BOUND
            free_cpus += soonest.cpus
            free_memory += soonest.memory_mb
            free_leases += 1
            holders.remove(soonest)

        if blind:
            estimates[waiter.id] = UNKNOWN_WAIT
            continue

        estimates[waiter.id] = WaitEstimate(seconds=int(round(clock)), basis=basis)
        # This waiter now occupies capacity, and the ones behind it must wait for
        # it too — the head-of-line rule, carried into the projection.
        free_cpus -= waiter.cpus
        free_memory -= waiter.memory_mb
        free_leases -= 1
        predicted = hold_stats.predict(waiter.footprint)
        holders.append(
            _Holder(
                cpus=waiter.cpus,
                memory_mb=waiter.memory_mb,
                releases_in=None if predicted is None else clock + predicted,
                basis=(DockerWaitBasis.UNKNOWN if predicted is None else DockerWaitBasis.HISTORY),
            )
        )

    return estimates


def _fits(lease: DockerLease, cpus: float, memory_mb: int, leases: int) -> bool:
    return lease.cpus <= cpus and lease.memory_mb <= memory_mb and leases >= 1


def poll_interval_for(estimate: WaitEstimate | None) -> int:
    """When a queued caller should ask again.

    Scaled to the wait rather than fixed, because a fixed interval is wrong in
    both directions at once: it wastes calls on a ten-minute wait and misses a
    lease that frees in three seconds. A quarter of the remaining wait means a
    caller checks a handful of times whatever the scale, and the bounds keep
    that from becoming either a spin or a nap.

    **A bound is polled harder than a forecast.** A TTL-derived estimate is an
    upper bound that the holder will almost certainly beat, so scaling off it the
    same way would have the first-ever caller sleeping two minutes for a lease
    that freed in twenty seconds. It gets an eighth, capped tighter.

    An unknown wait gets the floor, not the ceiling: we do not know that it is
    long, and a caller that has been told nothing should find out sooner.
    """
    floor = settings.docker_poll_min_interval_seconds
    ceiling = settings.docker_poll_max_interval_seconds
    if estimate is None or estimate.seconds is None:
        return int(floor)
    if estimate.basis is DockerWaitBasis.TTL_BOUND:
        return int(max(floor, min(ceiling / 4, estimate.seconds / 8)))
    return int(max(floor, min(ceiling, estimate.seconds / 4)))
