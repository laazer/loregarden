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
    CapacityPool,
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
    WaitEstimate,
    estimate_waits,
    poll_interval_for,
)
from loregarden.services.hold_history import (
    RECENT_HOLDS,
    Hold,
    HoldForecast,
    HoldStats,
    observed_hold_times,
)
from sqlmodel import Session, select

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


def _predict(
    stats: HoldStats,
    label: str,
    *,
    footprint: DockerFootprint = DockerFootprint.HEAVY,
    held_for: float = 0.0,
) -> HoldForecast | None:
    return stats.predict(CapacityPool.DOCKER, footprint, label, held_for=held_for)


def _expected(stats: HoldStats, footprint: DockerFootprint) -> float | None:
    """What a claim whose label nothing on record shares is predicted to hold for."""
    forecast = _predict(stats, "unheard-of", footprint=footprint)
    return forecast.expected if forecast else None


def _holds(label: str, *seconds: float, footprint=DockerFootprint.HEAVY) -> list[Hold]:
    return [
        Hold(pool=CapacityPool.DOCKER, footprint=footprint, label=label, seconds=each)
        for each in seconds
    ]


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
    assert _expected(stats, DockerFootprint.STACK) == pytest.approx(120)


def test_no_history_predicts_nothing(session) -> None:
    stats = observed_hold_times(session, now=NOW)
    assert stats.samples == 0
    assert _predict(stats, "anything", footprint=DockerFootprint.STACK) is None


def test_a_footprint_with_no_history_falls_back_to_everything(session) -> None:
    """Less than it knows about `stack`, more than nothing."""
    _history(session, footprint=DockerFootprint.STACK, held_seconds=200)
    stats = observed_hold_times(session, now=NOW)
    assert _expected(stats, DockerFootprint.HEAVY) == pytest.approx(200)


def test_a_suite_is_predicted_from_suites_not_from_pushes_of_its_size() -> None:
    """The footprint alone told the waiter behind a two-hour suite eight minutes:
    most `heavy` holds are six-minute pushes."""
    stats = HoldStats.of(
        _holds("pre-push server-tests · claude/a · pid 1", 2700, 2700, 2700)
        + _holds("dawnbringer pre-push · claude/b · pid 2", *[360] * 20)
    )

    suite = _predict(stats, "pre-push server-tests · claude/c · pid 9")
    push = _predict(stats, "dawnbringer pre-push · claude/d · pid 8")

    assert suite is not None and suite.expected == pytest.approx(2700)
    assert push is not None and push.expected == pytest.approx(360)


def test_a_label_seen_once_is_matched_by_the_words_it_leads_with() -> None:
    stats = HoldStats.of(
        _holds("dawnbringer push: plaid zero values", 300)
        + _holds("dawnbringer push: fix main", 360)
        + _holds("dawnbringer push: rename", 420)
        + _holds("server suite", 2700, 2700, 2700)
    )

    forecast = _predict(stats, "dawnbringer push: something new")

    assert forecast is not None and forecast.expected == pytest.approx(360)


def test_a_pr_number_does_not_make_it_different_work() -> None:
    stats = HoldStats.of(
        _holds("dawnbringer pre-push 1368", 300)
        + _holds("dawnbringer pre-push 1376", 300)
        + _holds("dawnbringer pre-push 1378", 300)
        + _holds("dawnbringer mutants", 9000, 9000, 9000)
    )

    forecast = _predict(stats, "dawnbringer pre-push 1401")

    assert forecast is not None and forecast.expected == pytest.approx(300)


def test_where_it_runs_is_not_part_of_what_it_is() -> None:
    """Keying by branch was tried, and predicted no better from far fewer holds."""
    stats = HoldStats.of(
        _holds("suite · claude/a · pid 1", *[100] * 3)
        + _holds("suite · claude/b · pid 2", *[900] * RECENT_HOLDS)
    )

    forecast = _predict(stats, "suite · claude/a · pid 7")

    assert forecast is not None and forecast.expected == pytest.approx(900)


def test_a_label_never_shares_history_across_pools() -> None:
    on_host = [Hold(CapacityPool.HOST, DockerFootprint.HEAVY, "suite", 9.0)] * 3
    stats = HoldStats.of(_holds("suite", 60, 60, 60) + on_host)

    forecast = _predict(stats, "suite")

    assert forecast is not None and forecast.expected == pytest.approx(60)


def test_only_the_latest_like_holds_count() -> None:
    """A suite that took 20 minutes two weeks ago takes 45 now."""
    stats = HoldStats.of(_holds("suite", *[1200] * 50, *[2700] * RECENT_HOLDS))

    forecast = _predict(stats, "suite")

    assert forecast is not None and forecast.expected == pytest.approx(2700)


def test_the_expected_hold_is_the_mean_because_waits_are_sums() -> None:
    """Ten in line, one in three of them a suite: the line takes 8000s, and the
    median of each hold would have promised it in 1000s."""
    stats = HoldStats.of(_holds("server-tests", 100, 100, 2200))

    forecast = _predict(stats, "server-tests")

    assert forecast is not None and forecast.expected == pytest.approx(800)


def test_a_forecast_carries_the_spread_of_its_history() -> None:
    stats = HoldStats.of(_holds("suite", 100, 200, 300, 400, 500, 600, 700, 800, 900, 1000))

    forecast = _predict(stats, "suite", held_for=0)

    assert forecast == HoldForecast(expected=550, low=200, high=1000)


def test_a_label_with_too_few_holds_defers_to_its_footprint() -> None:
    """Two runs of a new command are a worse guess than every claim its size."""
    stats = HoldStats.of(
        _holds("horizon build", 60, 60) + _holds("server suite", *[2700] * RECENT_HOLDS)
    )

    forecast = _predict(stats, "horizon build")

    assert forecast is not None and forecast.expected == pytest.approx(2700)


def test_too_little_history_has_no_spread() -> None:
    """Two holds of the same length are not evidence that the third will match."""
    stats = HoldStats.of(_holds("suite", 100, 100))

    assert _predict(stats, "suite") == HoldForecast(expected=100, low=None, high=None)


def test_a_holder_past_its_like_is_predicted_from_broader_work_that_ran_longer() -> None:
    stats = HoldStats.of(
        _holds("pre-push server-tests", 300, 300, 300)
        + _holds("pre-push everything", 4000, 4000, 4000)
    )

    forecast = _predict(stats, "pre-push server-tests", held_for=1000)

    assert forecast is not None and forecast.expected == pytest.approx(3000)


def test_history_is_read_in_release_order(session) -> None:
    """Rows come back in insertion order unless asked otherwise, and the latest
    holds are the ones that count."""
    for hours_ago, held in [*((1, 2700) for _ in range(RECENT_HOLDS)), *((300, 60),) * 20]:
        session.add(
            DockerLease(
                status=DockerLeaseStatus.RELEASED,
                holder_label="suite",
                footprint=DockerFootprint.HEAVY,
                end_reason=DockerLeaseEndReason.RELEASED,
                granted_at=NOW - timedelta(hours=hours_ago, seconds=held),
                released_at=NOW - timedelta(hours=hours_ago),
            )
        )
    session.commit()

    forecast = _predict(observed_hold_times(session, now=NOW), "suite")

    assert forecast is not None and forecast.expected == pytest.approx(2700)


def test_a_hold_from_before_the_lookback_teaches_nothing(session) -> None:
    for days_ago in (29, 31):
        session.add(
            DockerLease(
                status=DockerLeaseStatus.RELEASED,
                holder_label="suite",
                footprint=DockerFootprint.HEAVY,
                end_reason=DockerLeaseEndReason.RELEASED,
                granted_at=NOW - timedelta(days=days_ago, seconds=60),
                released_at=NOW - timedelta(days=days_ago),
            )
        )
    session.commit()

    assert observed_hold_times(session, now=NOW).samples == 1


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


def test_a_holder_past_the_median_is_estimated_from_the_holds_that_ran_longer(session) -> None:
    """A plain median said a 40-minute suite, 30 minutes in, would finish in 0s,
    and every waiter behind it read "starts in ≈ 0s" for most of an hour."""
    _history(session, footprint=DockerFootprint.CUSTOM, held_seconds=10, count=3)
    _history(session, footprint=DockerFootprint.CUSTOM, held_seconds=2400, count=1)
    _history(session, footprint=DockerFootprint.CUSTOM, held_seconds=2700, count=1)
    holder = _reserve(session, "holder", cpus=4.0, memory_mb=4096)
    waiter = _reserve(session, "waiter", cpus=4.0, memory_mb=4096)
    _granted_at(session, holder.lease_id, seconds_ago=1800)

    estimate = estimate_waits(session, now=NOW)[waiter.lease_id]

    # Of the holds longer than 1800s (2400, 2700) the median is 2550: 750s left.
    assert estimate.seconds == pytest.approx(750, abs=2)
    assert estimate.basis is DockerWaitBasis.HISTORY


def test_a_holder_that_outlived_all_history_falls_back_to_its_ttl(session) -> None:
    _history(session, footprint=DockerFootprint.CUSTOM, held_seconds=300)
    holder = _reserve(session, "holder", cpus=4.0, memory_mb=4096)
    waiter = _reserve(session, "waiter", cpus=4.0, memory_mb=4096)
    _granted_at(session, holder.lease_id, seconds_ago=1000)
    lease = session.get(DockerLease, holder.lease_id)
    lease.expires_at = NOW + timedelta(seconds=450)
    session.add(lease)
    session.commit()

    estimate = estimate_waits(session, now=NOW)[waiter.lease_id]

    assert estimate.seconds == pytest.approx(450, abs=2)
    assert estimate.basis is DockerWaitBasis.TTL_BOUND


def test_a_command_that_never_ran_teaches_nothing(session) -> None:
    _history(session, footprint=DockerFootprint.HEAVY, held_seconds=1200)
    session.add(
        DockerLease(
            status=DockerLeaseStatus.RELEASED,
            holder_label="pre-push client-tests",
            footprint=DockerFootprint.HEAVY,
            end_reason=DockerLeaseEndReason.COMMAND_NOT_RUN,
            granted_at=NOW - timedelta(seconds=61),
            released_at=NOW - timedelta(seconds=60),
        )
    )
    session.commit()

    stats = observed_hold_times(session, now=NOW)

    assert stats.samples == 3
    assert _expected(stats, DockerFootprint.HEAVY) == pytest.approx(1200)


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


# ---- the range ----------------------------------------------------------


def _one_holder_one_waiter(session, label="suite") -> str:
    holder = _reserve(session, label, cpus=4.0, memory_mb=4096)
    waiter = _reserve(session, "waiter", cpus=4.0, memory_mb=4096)
    _granted_at(session, holder.lease_id, seconds_ago=0)
    return waiter.lease_id


def test_a_wait_carries_the_range_its_holders_history_spans(session) -> None:
    waiter = _one_holder_one_waiter(session)
    stats = HoldStats.of(_holds("suite", 600, 700, 800, 900, 4000))

    estimate = estimate_waits(session, now=NOW, stats=stats)[waiter]

    assert (estimate.low_seconds, estimate.seconds, estimate.high_seconds) == (600, 1400, 4000)
    assert estimate.as_dict() == {
        "estimated_wait_seconds": 1400,
        "estimated_wait_low_seconds": 600,
        "estimated_wait_high_seconds": 4000,
        "estimate_basis": "history",
    }


def test_the_range_widens_down_the_line(session) -> None:
    _reserve(session, "suite", cpus=4.0, memory_mb=4096)
    first = _reserve(session, "suite", cpus=4.0, memory_mb=4096)
    second = _reserve(session, "suite", cpus=4.0, memory_mb=4000)
    for lease in session.exec(select(DockerLease).where(DockerLease.status == "held")):
        _granted_at(session, lease.id, seconds_ago=0)
    stats = HoldStats.of(_holds("suite", 600, 900, 1500))

    estimates = estimate_waits(session, now=NOW, stats=stats)

    near, far = estimates[first.lease_id], estimates[second.lease_id]
    assert (near.low_seconds, near.high_seconds) == (600, 1500)
    assert (far.low_seconds, far.high_seconds) == (1200, 3000)


def test_a_wait_resting_on_too_little_history_has_no_range(session) -> None:
    waiter = _one_holder_one_waiter(session)
    stats = HoldStats.of(_holds("suite", 600, 700))

    estimate = estimate_waits(session, now=NOW, stats=stats)[waiter]

    assert estimate == WaitEstimate(seconds=650, basis=DockerWaitBasis.HISTORY)


def test_a_bound_has_no_range(session) -> None:
    """ "Likely 7m–15m" about a TTL would present a ceiling as a forecast."""
    waiter = _one_holder_one_waiter(session)

    estimate = estimate_waits(session, now=NOW)[waiter]

    assert estimate.basis is DockerWaitBasis.TTL_BOUND
    assert (estimate.low_seconds, estimate.high_seconds) == (None, None)


@pytest.mark.parametrize(
    ("estimate", "said"),
    [
        (UNKNOWN_WAIT, "no estimate yet"),
        (WaitEstimate(seconds=900, basis=DockerWaitBasis.TTL_BOUND), "at most 15m"),
        (WaitEstimate(seconds=45, basis=DockerWaitBasis.HISTORY), "about 45s"),
        (
            WaitEstimate(
                seconds=2405, basis=DockerWaitBasis.HISTORY, low_seconds=722, high_seconds=5400
            ),
            "about 40m (likely 12m–1h30m)",
        ),
        (
            WaitEstimate(
                seconds=600, basis=DockerWaitBasis.HISTORY, low_seconds=600, high_seconds=600
            ),
            "about 10m",
        ),
    ],
)
def test_a_wait_is_said_to_the_minute_with_its_range(estimate, said) -> None:
    assert estimate.describe() == said


# ---- outside load -------------------------------------------------------


@pytest.fixture(name="busy_machine")
def busy_machine_fixture(session):
    """Room in the ledger for everything here, and the cores busy with something else."""
    host = docker_leases.load_pool(session, CapacityPool.HOST)
    host.ceiling_cpus = 7.0
    host.ceiling_memory_mb = 16384
    host.ceiling_leases = 6
    host.ceiling_source = DockerCeilingSource.CONFIG_OVERRIDE
    host.external_cpus = 6.0
    session.add(host)
    session.commit()


def _queue_on_host(session, label, *, cpus=2.0, waited: float = 0.0) -> str:
    """A waiter written directly: `reserve` would drain it against today's clock."""
    lease = DockerLease(
        status=DockerLeaseStatus.WAITING,
        holder_label=label,
        pool=CapacityPool.HOST,
        cpus=cpus,
        memory_mb=512,
        position=1,
        requested_at=NOW - timedelta(seconds=waited),
    )
    session.add(lease)
    session.commit()
    return lease.id


def _hold_on_host(session, label, *, cpus=1.0, held: float = 0.0) -> None:
    host = docker_leases.load_pool(session, CapacityPool.HOST)
    host.held_cpus += cpus
    host.held_memory_mb += 512
    host.held_count += 1
    session.add(host)
    session.add(
        DockerLease(
            status=DockerLeaseStatus.HELD,
            holder_label=label,
            pool=CapacityPool.HOST,
            cpus=cpus,
            memory_mb=512,
            granted_at=NOW - timedelta(seconds=held),
            expires_at=NOW + timedelta(seconds=900),
        )
    )
    session.commit()


def _long_holds() -> HoldStats:
    return HoldStats.of([Hold(CapacityPool.HOST, DockerFootprint.CUSTOM, "suite", 5000.0)] * 3)


def test_a_claim_with_room_still_waits_out_outside_load(session, busy_machine) -> None:
    """It was told "0s" and then waited the ten minutes the load rule holds it back."""
    _hold_on_host(session, "suite")
    waiter = _queue_on_host(session, "waiter", waited=150)

    estimate = estimate_waits(session, now=NOW, stats=_long_holds())[waiter]

    assert estimate.seconds == 450


def test_the_load_rule_stops_counting_when_the_ledger_empties(session, busy_machine) -> None:
    _hold_on_host(session, "suite", held=4900)
    waiter = _queue_on_host(session, "waiter")

    estimate = estimate_waits(session, now=NOW, stats=_long_holds())[waiter]

    assert estimate.seconds == 100


def test_a_small_claim_is_not_projected_to_wait_for_load(session, busy_machine) -> None:
    _hold_on_host(session, "suite")
    waiter = _queue_on_host(session, "agent", cpus=0.25)

    assert estimate_waits(session, now=NOW, stats=_long_holds())[waiter].seconds == 0


def test_a_claim_past_its_patience_is_judged_on_room_alone(session, busy_machine) -> None:
    _hold_on_host(session, "suite")
    waiter = _queue_on_host(session, "waiter", waited=700)

    assert estimate_waits(session, now=NOW, stats=_long_holds())[waiter].seconds == 0


def test_load_the_claim_fits_beside_does_not_hold_it_back(session, busy_machine) -> None:
    host = docker_leases.load_pool(session, CapacityPool.HOST)
    host.external_cpus = 3.0
    session.add(host)
    session.commit()
    _hold_on_host(session, "suite")
    waiter = _queue_on_host(session, "waiter", cpus=2.0)

    assert estimate_waits(session, now=NOW, stats=_long_holds())[waiter].seconds == 0


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
    stats = HoldStats(
        by_footprint={(CapacityPool.DOCKER, DockerFootprint.CUSTOM): (60.0,)}, overall=(60.0,)
    )

    assert estimate_waits(session, now=NOW, stats=stats)[waiter.lease_id].seconds == pytest.approx(
        60, abs=2
    )
