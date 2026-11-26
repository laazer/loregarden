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

**What predicts a holder's release.** The holds of work like it that ran longer
than this one already has (`hold_history`), less the time it has already held.
When no recorded hold ran that long, its remaining TTL stands in, which is a bound
rather than an estimate and is marked as such by producing a coarser answer. A
holder with neither makes every waiter behind it unknown, rather than being
skipped — skipping it would quietly predict a queue that cannot move.

**A range, because one number is not honest here.** Replayed over 2026-11-16..25
the estimate was within 30% (or 5 minutes) of the real wait 55% of the time; how
long a suite takes depends on what else the machine is doing, and no history says
what that will be. So the line is walked three times — every hold at its expected
length, at the short end of its history and at the long end. All at once, rather
than each hold drawn on its own, because a busy machine slows them together:
independent draws covered 65% of replayed waits where this covers 80%.

**Outside load is part of the wait.** The drain holds a claim of a core or more back
for up to `host_load_patience_seconds` while the machine is busy with something the
ledger does not hold. Left out, the first claim in an unoccupied line was told "0s"
and then waited ten minutes. The load is taken to last, since nothing predicts it.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone

from loregarden.config import settings
from loregarden.models.domain import (
    CapacityPool,
    DockerLease,
    DockerLeaseStatus,
    DockerWaitBasis,
)
from loregarden.services.capacity_passing import (
    Room,
    is_standing,
    may_pass_head,
    passing_leaves_head_alone,
)
from loregarden.services.capacity_progress import format_seconds
from loregarden.services.docker_ledger import (
    CHARGED_POOLS,
    OCCUPYING,
    as_utc,
    booked,
    load_patience_seconds,
    load_pool,
)
from loregarden.services.hold_history import HoldForecast, HoldStats, observed_hold_times
from sqlmodel import Session, col, select

#: Seconds too few to wait for. The projected clock is a float sum, so a wait that
#: is over can read as a hair short of it, and advancing by that hair goes nowhere.
_A_MOMENT = 0.001

#: Which end of a forecast a projection assumes for every hold it waits on.
Assume = Callable[[HoldForecast], float]


def _expected(forecast: HoldForecast) -> float:
    return forecast.expected


def _short(forecast: HoldForecast) -> float:
    return forecast.expected if forecast.low is None else forecast.low


def _long(forecast: HoldForecast) -> float:
    return forecast.expected if forecast.high is None else forecast.high


@dataclass(frozen=True)
class WaitEstimate:
    """How long a waiter will wait, and how much that number is worth.

    `seconds` alone cannot express the difference between "six minutes, measured"
    and "at most fifteen, because that is the TTL nobody has beaten yet", and the
    two call for different polling. `basis` says which, and `UNKNOWN` keeps
    `seconds` at None rather than letting a stand-in constant in.

    `low_seconds` and `high_seconds` are the range it is likely to fall in, about
    eight waits in ten. None where there is none to state: the answer is a bound,
    or too little history stands behind some part of it.
    """

    seconds: int | None
    basis: DockerWaitBasis
    low_seconds: int | None = None
    high_seconds: int | None = None

    def as_dict(self) -> dict:
        return {
            "estimated_wait_seconds": self.seconds,
            "estimated_wait_low_seconds": self.low_seconds,
            "estimated_wait_high_seconds": self.high_seconds,
            "estimate_basis": self.basis.value,
        }

    def describe(self) -> str:
        """ "about 40m (likely 12m–1h30m)", "at most 15m", or "no estimate yet"."""
        if self.seconds is None:
            return "no estimate yet"
        if self.basis is DockerWaitBasis.TTL_BOUND:
            # A bound from the holders' TTLs, which they almost always beat.
            return f"at most {_rough(self.seconds)}"
        about = f"about {_rough(self.seconds)}"
        if self.low_seconds is None or self.high_seconds is None:
            return about
        low, high = _rough(self.low_seconds), _rough(self.high_seconds)
        return about if low == high else f"{about} (likely {low}–{high})"


def _rough(seconds: int) -> str:
    """A wait to the minute once it is minutes: the seconds are not known."""
    if seconds < 90:
        return format_seconds(seconds)
    minutes = round(seconds / 60)
    return f"{minutes}m" if minutes < 60 else format_seconds(minutes * 60)


UNKNOWN_WAIT = WaitEstimate(seconds=None, basis=DockerWaitBasis.UNKNOWN)


@dataclass
class _Holder:
    """A lease occupying capacity, and when it is expected to give it back."""

    cpus: float
    memory_mb: int
    #: Lease-count slots it frees: none for a child, which runs in its parent's.
    count: int
    #: The pools this holder's booking occupies.
    pools: tuple[CapacityPool, ...]
    #: Seconds from now. None when nothing can predict it — which propagates
    #: rather than being skipped.
    releases_in: float | None
    #: Whether `releases_in` came from measured history or from a TTL. A waiter
    #: is only as well-predicted as the weakest holder it had to wait for.
    basis: DockerWaitBasis = DockerWaitBasis.UNKNOWN
    #: Held for a run's lifetime rather than for work that finishes.
    standing: bool = False
    #: Whether enough history stands behind `releases_in` to say how far off it
    #: may be. A waiter's range needs this of every holder it waited for.
    spread_known: bool = False


def _holders(
    leases: Sequence[DockerLease], stats: HoldStats, *, now: datetime, assume: Assume
) -> list[_Holder]:
    holders: list[_Holder] = []
    for lease in leases:
        booking = booked(lease)
        granted = as_utc(lease.granted_at)
        forecast = (
            stats.predict(
                lease.pool,
                lease.footprint,
                lease.holder_label,
                held_for=max(0.0, (now - granted).total_seconds()),
            )
            if granted is not None
            else None
        )
        releases_in: float | None = None
        basis = DockerWaitBasis.UNKNOWN
        if forecast is not None:
            releases_in = assume(forecast)
            basis = DockerWaitBasis.HISTORY
        else:
            expires = as_utc(lease.expires_at)
            if expires is not None:
                releases_in = max(0.0, (expires - now).total_seconds())
                basis = DockerWaitBasis.TTL_BOUND
        holders.append(
            _Holder(
                cpus=booking.cpus,
                memory_mb=booking.memory_mb,
                count=booking.count,
                pools=CHARGED_POOLS[lease.pool],
                releases_in=releases_in,
                basis=basis,
                standing=is_standing(lease),
                spread_known=forecast is not None and forecast.low is not None,
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
    Head-of-line, matching `drain_waiters`: a waiter is never projected to start
    before the one ahead of it, even when it would fit sooner, because the drain
    will not grant it sooner. The exception is the drain's too: a small standing
    claim is projected past a blocked head when it may pass (`capacity_passing`).
    """
    stamp = now or datetime.now(timezone.utc)
    hold_stats = stats if stats is not None else observed_hold_times(session, now=stamp)
    rows = {name: load_pool(session, name) for name in CapacityPool}
    host = rows[CapacityPool.HOST]
    waiters = session.exec(
        select(DockerLease)
        .where(DockerLease.status == DockerLeaseStatus.WAITING)
        .order_by(col(DockerLease.position))
    ).all()
    holding = session.exec(select(DockerLease).where(col(DockerLease.status).in_(OCCUPYING))).all()

    def project(assume: Assume) -> _Projection:
        projection = _Projection(
            stats=hold_stats,
            waiters=waiters,
            holders=_holders(holding, hold_stats, now=stamp, assume=assume),
            free={
                name: _Free(
                    cpus=row.ceiling_cpus - row.held_cpus,
                    memory_mb=row.ceiling_memory_mb - row.held_memory_mb,
                    leases=row.ceiling_leases - row.held_count,
                )
                for name, row in rows.items()
            },
            host_ceiling=Room(host.ceiling_cpus, host.ceiling_memory_mb),
            external_cpus=host.external_cpus,
            started_at=stamp,
            assume=assume,
        )
        projection.run()
        return projection

    expected, short, long = project(_expected), project(_short), project(_long)
    return {
        lease_id: _with_range(
            estimate,
            short.estimates[lease_id],
            long.estimates[lease_id],
            spread_known=not any(lease_id in p.spread_unknown for p in (expected, short, long)),
        )
        for lease_id, estimate in expected.estimates.items()
    }


def _with_range(
    expected: WaitEstimate, short: WaitEstimate, long: WaitEstimate, *, spread_known: bool
) -> WaitEstimate:
    """`expected` with its range, where every projection has an honest end to give.

    A bound has none: the holder it rests on has no spread. Releases can come in a
    different order at the long end, so a later holder may let a claim in sooner
    there; the ends are taken as they fall.
    """
    ends = [each.seconds for each in (expected, short, long) if each.seconds is not None]
    if not spread_known or len(ends) < 3:
        return expected
    return replace(expected, low_seconds=min(ends), high_seconds=max(ends))


@dataclass
class _Free:
    """One pool's unbooked capacity, as the projection walks the queue."""

    cpus: float
    memory_mb: int
    leases: int

    def fits(self, lease: DockerLease) -> bool:
        return (
            lease.cpus <= self.cpus
            and lease.memory_mb <= self.memory_mb
            and self.leases >= booked(lease).count
        )

    def add(self, cpus: float, memory_mb: int, leases: int) -> None:
        self.cpus += cpus
        self.memory_mb += memory_mb
        self.leases += leases


@dataclass
class _Projection:
    """The queue walked forward: who holds what, what is free, and when it is now."""

    stats: HoldStats
    waiters: Sequence[DockerLease]
    holders: list[_Holder]
    free: dict[CapacityPool, _Free]
    host_ceiling: Room
    #: Busy cores outside the ledger, taken to stay as they are now.
    external_cpus: float
    started_at: datetime
    assume: Assume
    clock: float = 0.0
    #: Degrades to TTL_BOUND the moment any holder we wait on was predicted from
    #: a TTL. A chain is only as strong as its weakest link, and presenting a
    #: bound as a forecast is the failure this whole field exists to prevent.
    basis: DockerWaitBasis = DockerWaitBasis.HISTORY
    #: Once a holder with no prediction has to be waited on, nothing behind it
    #: can be estimated either. Sticky rather than per-waiter: the queue is
    #: ordered, so an unpredictable blocker blocks everything after it.
    blind: bool = False
    #: Set once a holder with too little history to have a spread was waited on.
    #: Sticky for the same reason `blind` is.
    thin: bool = False
    estimates: dict[str, WaitEstimate] = field(default_factory=dict)
    #: Waiters whose estimate rests on a holder with no spread.
    spread_unknown: set[str] = field(default_factory=set)

    def run(self) -> None:
        for waiter in self.waiters:
            if waiter.id in self.estimates:
                continue  # already projected past an earlier head
            while not self.blind and not self._fits(waiter):
                for passing in self._passing(waiter):
                    self._start(passing)
                self._advance(waiter)
            if self.blind:
                self.estimates[waiter.id] = UNKNOWN_WAIT
            else:
                self._start(waiter)

    def _fits(self, waiter: DockerLease) -> bool:
        return self._has_room(waiter) and self._held_back_for(waiter) < _A_MOMENT

    def _has_room(self, waiter: DockerLease) -> bool:
        return all(self.free[name].fits(waiter) for name in CHARGED_POOLS[waiter.pool])

    def _held_back_for(self, waiter: DockerLease) -> float:
        """Seconds from the projected now that outside load keeps `waiter` out."""
        free_cpus = self.free[CapacityPool.HOST].cpus
        if waiter.cpus + self.external_cpus <= free_cpus:
            return 0.0
        return load_patience_seconds(
            waiter,
            external_cpus=self.external_cpus,
            held_cpus=self.host_ceiling.cpus - free_cpus,
            now=self.started_at + timedelta(seconds=self.clock),
        )

    def _advance(self, head: DockerLease) -> None:
        """Move to whatever may let `head` in next: a release, or the load rule's patience.

        Only a head with room is waiting on load alone. If something releases
        first it is taken first: an empty ledger lets the head in at any load.
        """
        if self._has_room(head):
            patience_ends = self.clock + self._held_back_for(head)
            next_release = min(
                (h.releases_in for h in self.holders if h.releases_in is not None),
                default=patience_ends,
            )
            if patience_ends <= next_release:
                self.clock = patience_ends
                return
        self._release_soonest()

    def _start(self, waiter: DockerLease) -> None:
        self.estimates[waiter.id] = WaitEstimate(seconds=int(round(self.clock)), basis=self.basis)
        if self.thin:
            self.spread_unknown.add(waiter.id)
        # This waiter now occupies capacity, and the ones behind it must wait for
        # it too — the head-of-line rule, carried into the projection.
        for name in CHARGED_POOLS[waiter.pool]:
            self.free[name].add(-waiter.cpus, -waiter.memory_mb, -booked(waiter).count)
        forecast = self.stats.predict(
            waiter.pool, waiter.footprint, waiter.holder_label, held_for=0.0
        )
        self.holders.append(
            _Holder(
                cpus=waiter.cpus,
                memory_mb=waiter.memory_mb,
                count=booked(waiter).count,
                pools=CHARGED_POOLS[waiter.pool],
                releases_in=None if forecast is None else self.clock + self.assume(forecast),
                basis=(DockerWaitBasis.UNKNOWN if forecast is None else DockerWaitBasis.HISTORY),
                standing=is_standing(waiter),
                spread_known=forecast is not None and forecast.low is not None,
            )
        )

    def _release_soonest(self) -> None:
        """Move to the next release, or go blind when nothing honest says when that is."""
        pending = [h for h in self.holders if h.releases_in is not None]
        if not pending or len(pending) < len(self.holders):
            # Nothing is holding (so the ceiling itself is too small, which
            # admission refuses rather than queues), or a holder is
            # unpredictable: it might free capacity sooner than the one we are
            # about to wait for, so this estimate would be an upper bound
            # presented as a prediction. Neither leaves an honest number.
            self.blind = True
            return
        soonest = min(pending, key=lambda h: h.releases_in or 0.0)
        self.clock = max(self.clock, soonest.releases_in or 0.0)
        if soonest.basis is DockerWaitBasis.TTL_BOUND:
            self.basis = DockerWaitBasis.TTL_BOUND
        self.thin = self.thin or not soonest.spread_known
        for name in soonest.pools:
            self.free[name].add(soonest.cpus, soonest.memory_mb, soonest.count)
        self.holders.remove(soonest)

    def _passing(self, head: DockerLease) -> Iterator[DockerLease]:
        """The waiters that may start ahead of `head` at this point of the projection.

        `drain_waiters`' own rule, asked of the projected pool. Lazy on purpose:
        the caller starts each one before the next is judged.
        """
        for waiter in self.waiters:
            if waiter.position <= head.position or waiter.id in self.estimates:
                continue
            if not may_pass_head(waiter):
                continue
            if not self._leaves_alone(head, waiter):
                return  # passing claims keep their own order, as in the drain
            yield waiter

    def _leaves_alone(self, head: DockerLease, waiter: DockerLease) -> bool:
        free = self.free[CapacityPool.HOST]
        return free.fits(waiter) and passing_leaves_head_alone(
            ceiling=self.host_ceiling,
            held=self.host_ceiling - Room(free.cpus, free.memory_mb),
            work=[
                Room(holder.cpus, holder.memory_mb)
                for holder in self.holders
                if not holder.standing and CapacityPool.HOST in holder.pools
            ],
            head=Room(head.cpus, head.memory_mb),
            claim=Room(waiter.cpus, waiter.memory_mb),
        )


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
