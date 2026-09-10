"""Back-pressure for callers polling a queued docker lease.

`poll_after_seconds` is advice, and advice is not a limit. An agent in a retry
loop, or three agents each waiting on the same full pool, can call the status
tool as fast as the transport allows — and every one of those calls runs a
promotion pass, which writes. The ledger would become its own bottleneck at
exactly the moment it is most contended.

**What throttling must not do is lie.** A throttled call still returns the
lease's real current state, because reading a row is cheap and a caller that
asked too soon still deserves the truth — telling it "slow down" *instead of*
telling it its lease was granted would strand work that already had capacity.
What a throttled call skips is the expensive half: the promotion pass and any
docker probe. Those are driven by the reconciliation timer regardless, so
skipping them delays nothing that was not already going to happen.

The counter is on the lease rather than in memory because the callers span
processes: the HTTP server and every in-process CLI invocation are different
processes, and an in-memory limiter would see each CLI call as the first.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from loregarden.config import settings
from loregarden.models.domain import DockerLease
from loregarden.services.docker_ledger import as_utc
from sqlmodel import Session


@dataclass(frozen=True)
class PollDecision:
    """Whether this caller may do the expensive work, and what to tell it."""

    throttled: bool
    retry_after_seconds: int
    poll_count: int

    def as_dict(self) -> dict:
        return {
            "throttled": self.throttled,
            "retry_after_seconds": self.retry_after_seconds,
            "poll_count": self.poll_count,
        }


def note_poll(
    session: Session,
    lease: DockerLease,
    *,
    now: datetime | None = None,
    min_interval: float | None = None,
) -> PollDecision:
    """Record that this lease was polled, and say whether it was too soon.

    Records the attempt either way. A caller that polls ten times a second is a
    fact worth having on the row — it is the difference between "the queue is
    slow" and "something is spinning on it", and only one of those is fixed by
    adding capacity.
    """
    stamp = now or datetime.now(timezone.utc)
    interval = settings.docker_poll_min_interval_seconds if min_interval is None else min_interval

    last = as_utc(lease.last_polled_at)
    since = None if last is None else (stamp - last).total_seconds()
    too_soon = since is not None and since < interval

    lease.poll_count += 1
    if too_soon:
        lease.throttled_poll_count += 1
    else:
        # Only a poll we actually served advances the clock. Stamping it on a
        # refused one would let a tight loop hold the window open forever: every
        # call would arrive "too soon" after the last refusal, and the caller
        # would never get served at all.
        lease.last_polled_at = stamp
    session.add(lease)
    session.commit()

    retry_after = int(round(max(0.0, interval - (since or 0.0)))) if too_soon else 0
    return PollDecision(
        throttled=too_soon,
        retry_after_seconds=max(1, retry_after) if too_soon else 0,
        poll_count=lease.poll_count,
    )
