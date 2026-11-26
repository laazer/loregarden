"""How long work like this has held capacity here: the history waits are estimated from.

Every choice below was back-tested by replaying the ledger — 1375 estimates for
the 575 claims that waited over 2026-11-16..25, and the choice then checked on
2026-11-10..15. Replaying with the true hold times was right to 0 minutes at the
median, so the projection was sound and the history was the error.

**Keyed by what runs, not by how big it is.** A footprint mixes work that shares
a size and nothing else: `heavy` is a 45-minute server suite and a 6-minute push.
But labels are free text and half of them are seen once, so a label is matched by
its longest run of leading words that enough past holds share
(`capacity_label.label_words`): "dawnbringer push: plaid zero values" is predicted
from the "dawnbringer push" holds. Then the footprint, then everything — a machine
with history for `stack` and none for `heavy` knows more than nothing about `heavy`.

**The last `RECENT_HOLDS` of them.** A suite that took 20 minutes two weeks ago
takes 45 now; a window of days alone is either too short for a rare label or too
long for a daily one, so the lookback only bounds how far back a rare one reaches.

**Their mean, not their median.** A wait is a sum of holds, and the sum's expected
value is the sum of the means. Holds are skewed, so medians summed down a line of
eight fall short by the same skew eight times.

**Conditional on having lasted this long.** Nothing about a suite 30 minutes in is
learned from runs that ended in five.

**Tried and left out**, because they did not help: scaling by how busy the machine
was at the grant, counting holds still running as censored observations, keying by
branch. A holder's reported progress could not be tested: the ledger keeps only
the last report.

**Only clean releases count.** A lease reclaimed at `TTL_EXPIRED` or `ABANDONED`
records the moment the reaper noticed, not the moment the work finished, so
folding those in would teach the estimator that every claim lasts exactly one TTL.
`COMMAND_NOT_RUN` is excluded for the opposite reason: a command the shell could
not start releases in a fraction of a second, and sixteen of those once dragged
the `heavy` history for a test suite down to seconds.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from statistics import fmean

from loregarden.config import settings
from loregarden.models.domain import (
    CapacityPool,
    DockerFootprint,
    DockerLease,
    DockerLeaseEndReason,
    DockerLeaseStatus,
)
from loregarden.services.capacity_label import label_words
from loregarden.services.docker_ledger import as_utc
from sqlalchemy.orm import load_only
from sqlmodel import Session, col, select

#: End reasons whose `released_at` marks the work finishing rather than the
#: reaper noticing it had not. Only these teach the estimator anything.
CLEAN_RELEASES = (DockerLeaseEndReason.RELEASED, DockerLeaseEndReason.RUN_COMPLETED)

#: How many of the latest like holds a prediction is drawn from.
RECENT_HOLDS = 10

#: Like holds needed before a group is preferred to a broader one, and before
#: its spread is worth stating.
ENOUGH_HOLDS = 3

#: The tails the range leaves out. Projected at these for every holder at once,
#: 80% of replayed waits fell inside, 7% started sooner and 13% later.
_LOW_TAIL = 0.1
_HIGH_TAIL = 0.9


@dataclass(frozen=True)
class HoldForecast:
    """Seconds a hold has still to go: the expected figure, and its spread.

    `low` and `high` are None when too few holds stand behind `expected` to say
    anything about spread; three identical numbers would claim a certainty that
    one observation does not have.
    """

    expected: float
    low: float | None = None
    high: float | None = None


@dataclass(frozen=True)
class Hold:
    """One finished hold, and what it can be matched on."""

    pool: CapacityPool
    footprint: DockerFootprint
    label: str
    seconds: float


#: A run of a label's leading words, in one pool.
_LabelKey = tuple[CapacityPool, tuple[str, ...]]


@dataclass
class HoldStats:
    """The holds recorded here, by what ran, by footprint, and overall.

    The observations themselves rather than their averages, because the useful
    question about a holder already part-way through is conditional: of the
    holds that lasted at least this long, how long did they last?

    Every tuple is in release order, latest last.
    """

    #: Keyed by pool too: a `heavy` test suite and a `heavy` docker stack share
    #: a size, not a duration.
    by_label: dict[_LabelKey, list[float]] = field(default_factory=dict)
    by_footprint: dict[tuple[CapacityPool, DockerFootprint], list[float]] = field(
        default_factory=dict
    )
    overall: list[float] = field(default_factory=list)

    @classmethod
    def of(cls, holds: Sequence[Hold]) -> HoldStats:
        """Index `holds`, which must be in release order."""
        stats = cls()
        for hold in holds:
            words = label_words(hold.label)
            for length in range(1, len(words) + 1):
                stats.by_label.setdefault((hold.pool, words[:length]), []).append(hold.seconds)
            stats.by_footprint.setdefault((hold.pool, hold.footprint), []).append(hold.seconds)
            stats.overall.append(hold.seconds)
        return stats

    @property
    def samples(self) -> int:
        return len(self.overall)

    def predict(
        self, pool: CapacityPool, footprint: DockerFootprint, label: str, *, held_for: float
    ) -> HoldForecast | None:
        """How long a lease already held `held_for` seconds still has, or None.

        `held_for` is zero for a claim not yet started. None when nothing
        recorded ran that long: the history has nothing to say about a holder
        that has outlived all of it, and "any moment now" is the one answer it
        certainly cannot support.
        """
        like = self._like(pool, footprint, label, held_for=held_for)
        if not like:
            return None
        expected = fmean(like) - held_for
        if len(like) < ENOUGH_HOLDS:
            return HoldForecast(expected=expected)
        ranked = sorted(like)
        return HoldForecast(
            expected=expected,
            low=ranked[int(_LOW_TAIL * len(ranked))] - held_for,
            high=ranked[int(_HIGH_TAIL * len(ranked))] - held_for,
        )

    def _like(
        self, pool: CapacityPool, footprint: DockerFootprint, label: str, *, held_for: float
    ) -> list[float]:
        """The latest holds most like this one that lasted longer than `held_for`.

        The longest shared label run with enough of them, then the footprint,
        then everything. Where none has enough, the closest with any: two holds are
        thin evidence, and more than none.
        """
        thin: list[float] = []
        for group in self._groups(pool, footprint, label_words(label)):
            longer = _recent_longer(group, held_for)
            if len(longer) >= ENOUGH_HOLDS:
                return longer
            thin = thin or longer
        return thin

    def _groups(
        self, pool: CapacityPool, footprint: DockerFootprint, words: tuple[str, ...]
    ) -> Iterator[Sequence[float]]:
        """The holds to look among, most like this one first."""
        for length in range(len(words), 0, -1):
            yield self.by_label.get((pool, words[:length]), ())
        yield self.by_footprint.get((pool, footprint), ())
        yield self.overall


def _recent_longer(holds: Sequence[float], held_for: float) -> list[float]:
    """The latest `RECENT_HOLDS` of `holds` that ran longer than `held_for`."""
    return [held for held in holds if held > held_for][-RECENT_HOLDS:]


def observed_hold_times(session: Session, *, now: datetime | None = None) -> HoldStats:
    """Every hold its holder actually released within the lookback, latest last."""
    stamp = now or datetime.now(timezone.utc)
    cutoff = stamp - timedelta(days=settings.docker_wait_lookback_days)

    # Every waiter's poll asks this, over thousands of leases.
    leases = session.exec(
        select(DockerLease)
        .where(
            DockerLease.status == DockerLeaseStatus.RELEASED,
            col(DockerLease.end_reason).in_(CLEAN_RELEASES),
            col(DockerLease.granted_at).is_not(None),
            col(DockerLease.released_at) >= cutoff,
        )
        .order_by(col(DockerLease.released_at))
        .options(
            load_only(
                col(DockerLease.pool),
                col(DockerLease.footprint),
                col(DockerLease.holder_label),
                col(DockerLease.granted_at),
                col(DockerLease.released_at),
            )
        )
    ).all()

    holds: list[Hold] = []
    for lease in leases:
        granted = as_utc(lease.granted_at)
        released = as_utc(lease.released_at)
        if granted is None or released is None:
            continue
        held = (released - granted).total_seconds()
        if held > 0:
            holds.append(
                Hold(
                    pool=lease.pool,
                    footprint=lease.footprint,
                    label=lease.holder_label,
                    seconds=held,
                )
            )
    return HoldStats.of(holds)
