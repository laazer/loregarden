"""Projected waits, and the cases where the honest answer is no answer.

`run_duration_stats` set the rule this follows: the denominator is what leases
have actually cost on this machine, and with no history the answer is ``None``.
A constant default would put the fabrication back — and here an invented wait is
worse than a missing one, because the number is what a caller uses to decide how
often to poll. Two of the tests below exist only to pin that.

The simulation, rather than arithmetic on the queue length, is pinned by
`test_a_waiter_waits_for_the_holders_that_actually_unblock_it`: capacity is
two-dimensional, so "how many are ahead of me" is not a wait.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from loregarden.models.domain import (
    DockerCeilingSource,
    DockerFootprint,
    DockerLease,
    DockerLeaseEndReason,
    DockerLeaseStatus,
    DockerWaitBasis,
)
from loregarden.services import docker_leases
from loregarden.services.docker_wait_estimate import (
    UNKNOWN_WAIT,
    HoldStats,
    WaitEstimate,
    estimate_waits,
    observed_hold_times,
    poll_interval_for,
)
from sqlmodel import Session

NOW = datetime(2026, 9, 10, 12, 0, tzinfo=timezone.utc)


@pytest.fixture(name="session")
def session_fixture(isolated_db):
    with Session(isolated_db) as session:
        yield session


@pytest.fixture(name="ceiling", autouse=True)
def ceiling_fixture(session):
    pool = docker_leases.load_pool(session)
    pool.ceiling_cpus = 4.0
    pool.ceiling_memory_mb = 4096
    pool.ceiling_leases = 4
    pool.ceiling_source = DockerCeilingSource.CONFIG_OVERRIDE
    session.add(pool)
    session.commit()
    return pool


def _reserve(session, label, *, cpus=1.0, memory_mb=1024, footprint=DockerFootprint.CUSTOM):
    return docker_leases.reserve(
        session, holder_label=label, footprint=footprint, cpus=cpus, memory_mb=memory_mb
    )


def _granted_at(session, lease_id, *, seconds_ago: float) -> None:
    lease = session.get(DockerLease, lease_id)
    lease.granted_at = NOW - timedelta(seconds=seconds_ago)
    session.add(lease)
    session.commit()


def _history(session, *, footprint, held_seconds, count=3) -> None:
    """Leases somebody actually released, which is the only kind that teaches."""
    for index in range(count):
        session.add(
            DockerLease(
                status=DockerLeaseStatus.RELEASED,
                holder_label=f"history-{footprint.value}-{index}",
                footprint=footprint,
                cpus=1.0,
                memory_mb=1024,
                end_reason=DockerLeaseEndReason.RELEASED,
                granted_at=NOW - timedelta(seconds=held_seconds + 60),
                released_at=NOW - timedelta(seconds=60),
            )
        )
    session.commit()


# ---- history ------------------------------------------------------------


def test_only_clean_releases_teach_the_estimator(session) -> None:
    """A lease reclaimed at TTL records when the reaper noticed, not when the
    work finished. Folding those in teaches that every claim lasts one TTL."""
    _history(session, footprint=DockerFootprint.STACK, held_seconds=120)
    session.add(
        DockerLease(
            status=DockerLeaseStatus.RELEASED,
            holder_label="abandoned",
            footprint=DockerFootprint.STACK,
            end_reason=DockerLeaseEndReason.TTL_EXPIRED,
            granted_at=NOW - timedelta(seconds=9000),
            released_at=NOW,
        )
    )
    session.commit()

    stats = observed_hold_times(session, now=NOW)
    assert stats.samples == 3
    assert stats.by_footprint[DockerFootprint.STACK] == pytest.approx(120)


def test_no_history_predicts_nothing(session) -> None:
    stats = observed_hold_times(session, now=NOW)
    assert stats.samples == 0
    assert stats.overall is None
    assert stats.predict(DockerFootprint.STACK) is None


def test_a_footprint_with_no_history_falls_back_to_the_overall_median(session) -> None:
    """Less than it knows about `stack`, more than nothing."""
    _history(session, footprint=DockerFootprint.STACK, held_seconds=200)
    stats = observed_hold_times(session, now=NOW)
    assert stats.predict(DockerFootprint.HEAVY) == pytest.approx(200)


# ---- projection ---------------------------------------------------------


def test_a_waiter_is_projected_from_when_the_holder_will_release(session) -> None:
    _history(session, footprint=DockerFootprint.CUSTOM, held_seconds=300)
    holder = _reserve(session, "holder", cpus=4.0, memory_mb=4096)
    waiter = _reserve(session, "waiter", cpus=4.0, memory_mb=4096)
    assert waiter.state.value == "queued"
    _granted_at(session, holder.lease_id, seconds_ago=100)

    estimates = estimate_waits(session, now=NOW)
    # Held for 100s of a 300s median, so ~200s left.
    assert estimates[waiter.lease_id].seconds == pytest.approx(200, abs=2)


def test_with_no_history_the_wait_is_unknown_rather_than_invented(session) -> None:
    """The rule this module exists for. A caller that is told nothing polls
    sooner; a caller told a fabricated number trusts it."""
    holder = _reserve(session, "holder", cpus=4.0, memory_mb=4096)
    waiter = _reserve(session, "waiter", cpus=4.0, memory_mb=4096)
    lease = session.get(DockerLease, holder.lease_id)
    lease.expires_at = None
    session.add(lease)
    session.commit()

    assert estimate_waits(session, now=NOW)[waiter.lease_id].seconds is None


def test_without_history_the_remaining_ttl_is_used_as_a_bound(session) -> None:
    """A TTL is not a prediction, but it is the only thing on record, and it
    bounds the answer rather than inventing one."""
    holder = _reserve(session, "holder", cpus=4.0, memory_mb=4096)
    waiter = _reserve(session, "waiter", cpus=4.0, memory_mb=4096)
    lease = session.get(DockerLease, holder.lease_id)
    lease.expires_at = NOW + timedelta(seconds=450)
    session.add(lease)
    session.commit()

    assert estimate_waits(session, now=NOW)[waiter.lease_id].seconds == pytest.approx(450, abs=2)


def test_a_waiter_waits_for_the_holders_that_actually_unblock_it(session) -> None:
    """Why this is a simulation and not division.

    The ceiling is 4 cpus. Two 1-cpu holders and one 2-cpu holder are up, and a
    3-cpu waiter needs 3 back — so it waits for the *second* release, not the
    first, and not for all three. Counting positions cannot express that.
    """
    _history(session, footprint=DockerFootprint.CUSTOM, held_seconds=600)
    small = _reserve(session, "small", cpus=1.0, memory_mb=256)
    medium = _reserve(session, "medium", cpus=2.0, memory_mb=256)
    _reserve(session, "other", cpus=1.0, memory_mb=256)
    waiter = _reserve(session, "waiter", cpus=3.0, memory_mb=512)
    assert waiter.state.value == "queued"

    _granted_at(session, small.lease_id, seconds_ago=500)  # releases in ~100s
    _granted_at(session, medium.lease_id, seconds_ago=400)  # releases in ~200s

    # 1 cpu free after the first release is not enough; 3 free after the second is.
    assert estimate_waits(session, now=NOW)[waiter.lease_id].seconds == pytest.approx(200, abs=3)


def test_a_waiter_is_never_projected_ahead_of_the_one_in_front_of_it(session) -> None:
    """Head-of-line, carried into the projection. A small claim behind a large
    one will not start sooner, so it must not be told it will."""
    _history(session, footprint=DockerFootprint.CUSTOM, held_seconds=300)
    holder = _reserve(session, "holder", cpus=4.0, memory_mb=4096)
    big = _reserve(session, "big", cpus=4.0, memory_mb=4096)
    small = _reserve(session, "small", cpus=0.5, memory_mb=128)
    _granted_at(session, holder.lease_id, seconds_ago=0)

    estimates = estimate_waits(session, now=NOW)
    assert estimates[small.lease_id].seconds >= estimates[big.lease_id].seconds


# ---- poll interval ------------------------------------------------------


def _forecast(seconds: int) -> WaitEstimate:
    return WaitEstimate(seconds=seconds, basis=DockerWaitBasis.HISTORY)


def test_the_poll_interval_scales_with_the_wait() -> None:
    """A fixed interval is wrong in both directions at once: it wastes calls on
    a long wait and misses a lease that frees in seconds."""
    assert poll_interval_for(_forecast(400)) > poll_interval_for(_forecast(40))


def test_the_poll_interval_is_bounded_at_both_ends() -> None:
    assert poll_interval_for(_forecast(1)) >= 5
    assert poll_interval_for(_forecast(10_000)) <= 120


def test_an_unknown_wait_polls_sooner_not_later() -> None:
    """We do not know that it is long. A caller told nothing should find out."""
    assert poll_interval_for(None) == poll_interval_for(UNKNOWN_WAIT) == 5


def test_a_ttl_bound_is_polled_harder_than_a_forecast_of_the_same_length() -> None:
    """The flaw an end-to-end run exposed: with no history the estimate falls
    back to the holder's TTL, which is an upper bound the holder will almost
    certainly beat. Scaling off it like a forecast had the first-ever queued
    caller sleeping two minutes for a lease that freed in twenty seconds.
    """
    bound = WaitEstimate(seconds=900, basis=DockerWaitBasis.TTL_BOUND)
    assert poll_interval_for(bound) < poll_interval_for(_forecast(900))


def test_a_wait_built_on_any_ttl_bound_is_reported_as_a_bound(session) -> None:
    """A chain is as strong as its weakest link. One unmeasured holder in the
    way makes the whole projection an upper bound, and saying otherwise would
    present a ceiling as a forecast."""
    holder = _reserve(session, "holder", cpus=4.0, memory_mb=4096)
    waiter = _reserve(session, "waiter", cpus=4.0, memory_mb=4096)
    lease = session.get(DockerLease, holder.lease_id)
    lease.expires_at = NOW + timedelta(seconds=600)
    session.add(lease)
    session.commit()

    estimate = estimate_waits(session, now=NOW)[waiter.lease_id]
    assert estimate.basis is DockerWaitBasis.TTL_BOUND
    assert estimate.as_dict()["estimate_basis"] == "ttl_bound"


def test_a_wait_built_only_on_history_says_so(session) -> None:
    _history(session, footprint=DockerFootprint.CUSTOM, held_seconds=300)
    holder = _reserve(session, "holder", cpus=4.0, memory_mb=4096)
    waiter = _reserve(session, "waiter", cpus=4.0, memory_mb=4096)
    _granted_at(session, holder.lease_id, seconds_ago=0)

    assert estimate_waits(session, now=NOW)[waiter.lease_id].basis is DockerWaitBasis.HISTORY


def test_estimates_can_be_computed_from_supplied_stats(session) -> None:
    """The stats are injectable so a caller estimating a whole board pays for one
    history query, not one per waiter."""
    holder = _reserve(session, "holder", cpus=4.0, memory_mb=4096)
    waiter = _reserve(session, "waiter", cpus=4.0, memory_mb=4096)
    _granted_at(session, holder.lease_id, seconds_ago=0)
    stats = HoldStats(by_footprint={DockerFootprint.CUSTOM: 60.0}, overall=60.0, samples=9)

    assert estimate_waits(session, now=NOW, stats=stats)[waiter.lease_id].seconds == pytest.approx(
        60, abs=2
    )
