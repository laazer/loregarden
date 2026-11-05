"""Weekly token trend: per-run medians, unmeasured runs kept apart, and a
comparison that refuses to speak over too few runs."""

from datetime import date, datetime, timedelta

import pytest
from loregarden.models.domain import RunStatus
from loregarden.services.token_usage_trend import (
    COMPARE_WEEKS,
    MIN_RUNS_TO_COMPARE,
    UNATTRIBUTED_MODEL,
    TrendDirection,
    TrendFilter,
    TrendMetric,
    run_tokens,
    token_trend,
    week_start_of,
)
from sqlmodel import Session
from tests.factories import make_agent_run, make_workspace

WORKSPACE = "ws-trend"
# A Thursday, so "this week" started on Monday 2026-11-02.
NOW = datetime(2026, 11, 5, 12, 0)
THIS_WEEK = date(2026, 11, 2)


@pytest.fixture(name="session")
def session_fixture(isolated_db):
    with Session(isolated_db) as session:
        make_workspace(session, workspace_id=WORKSPACE, slug=WORKSPACE)
        yield session


_counter = iter(range(1_000_000))


def _run(
    session: Session,
    *,
    weeks_ago: int,
    tokens: int | None,
    model: str = "claude-opus-5",
    stage_key: str = "implement",
    status: RunStatus = RunStatus.SUCCEEDED,
    ticket_id: str | None = None,
) -> None:
    started = datetime.combine(THIS_WEEK, datetime.min.time()) - timedelta(weeks=weeks_ago)
    started += timedelta(hours=10)
    usage = {} if tokens is None else {"input_tokens": tokens, "output_tokens": 0}
    make_agent_run(
        session,
        workspace_id=WORKSPACE,
        run_code=f"RUN-{next(_counter)}",
        ticket_id=ticket_id,
        stage_key=stage_key,
        status=status,
        model=model,
        started_at=started,
        created_at=started,
        **usage,
    )


def _trend(session: Session, **flt):
    return token_trend(session, TrendFilter(**flt), now=NOW)


def test_week_start_is_monday():
    assert week_start_of(NOW) == THIS_WEEK
    assert week_start_of(datetime(2026, 11, 2, 0, 0)) == THIS_WEEK
    assert week_start_of(datetime(2026, 11, 1, 23, 59)) == date(2026, 10, 26)


def test_uncached_metric_drops_cache_reads(session):
    make_agent_run(
        session,
        workspace_id=WORKSPACE,
        run_code="RUN-cache",
        status=RunStatus.SUCCEEDED,
        started_at=NOW,
        created_at=NOW,
        input_tokens=10,
        output_tokens=5,
        cache_read_tokens=1000,
        cache_write_tokens=100,
    )
    unmeasured = make_agent_run(
        session, workspace_id=WORKSPACE, run_code="RUN-none", status=RunStatus.SUCCEEDED
    )
    trend = _trend(session, weeks=1, metric=TrendMetric.UNCACHED)
    assert trend.weeks[0].median_tokens_per_run == 115
    assert _trend(session, weeks=1).weeks[0].median_tokens_per_run == 1115
    assert run_tokens(unmeasured, TrendMetric.UNCACHED) is None


def test_every_week_present_oldest_first_current_marked_partial(session):
    _run(session, weeks_ago=2, tokens=100)
    trend = _trend(session, weeks=4)
    starts = [w.week_start for w in trend.weeks]
    assert starts == [THIS_WEEK - timedelta(weeks=n) for n in (3, 2, 1, 0)]
    assert [w.partial for w in trend.weeks] == [False, False, False, True]
    empty = trend.weeks[0]
    assert empty.totals.runs == 0
    assert empty.totals.total_tokens is None
    assert empty.median_tokens_per_run is None


def test_unmeasured_runs_counted_but_not_in_median(session):
    for tokens in (100, 200, 300):
        _run(session, weeks_ago=1, tokens=tokens, ticket_id="t-a")
    _run(session, weeks_ago=1, tokens=None, ticket_id="t-b")
    week = _trend(session, weeks=2).weeks[0]
    assert week.totals.runs == 4
    assert week.totals.measured_runs == 3
    assert week.totals.unmeasured_runs == 1
    assert week.median_tokens_per_run == 200
    assert week.tickets == 2


def test_in_flight_runs_excluded(session):
    _run(session, weeks_ago=1, tokens=None, status=RunStatus.RUNNING)
    _run(session, weeks_ago=1, tokens=500, status=RunStatus.FAILED)
    week = _trend(session, weeks=2).weeks[0]
    assert week.totals.runs == 1
    assert week.totals.unmeasured_runs == 0


def _fill(session, *, weeks_ago_range, tokens, count, **kw):
    for weeks_ago in weeks_ago_range:
        for _ in range(count):
            _run(session, weeks_ago=weeks_ago, tokens=tokens, **kw)


def test_comparison_reports_a_fall_in_tokens_per_run(session):
    recent = range(1, COMPARE_WEEKS + 1)
    prior = range(COMPARE_WEEKS + 1, 2 * COMPARE_WEEKS + 1)
    _fill(session, weeks_ago_range=prior, tokens=1000, count=6)
    # More runs, each cheaper: totals rise, per-run cost falls.
    _fill(session, weeks_ago_range=recent, tokens=600, count=20)
    # The partial current week is not part of either side.
    _fill(session, weeks_ago_range=[0], tokens=10, count=50)

    cmp = _trend(session, weeks=4).comparison
    assert cmp.prior_median == 1000
    assert cmp.recent_median == 600
    assert cmp.change_percent == -40.0
    assert cmp.direction is TrendDirection.DOWN
    assert cmp.recent_start == THIS_WEEK - timedelta(weeks=COMPARE_WEEKS)


def test_comparison_withheld_below_minimum_runs(session):
    _run(session, weeks_ago=COMPARE_WEEKS + 1, tokens=1000)
    _fill(session, weeks_ago_range=[1], tokens=10, count=MIN_RUNS_TO_COMPARE)
    cmp = _trend(session).comparison
    assert cmp.prior_measured_runs == 1
    assert cmp.change_percent is None
    assert cmp.direction is TrendDirection.UNKNOWN


def test_small_change_is_flat(session):
    _fill(session, weeks_ago_range=[COMPARE_WEEKS + 1], tokens=1000, count=MIN_RUNS_TO_COMPARE)
    _fill(session, weeks_ago_range=[1], tokens=1020, count=MIN_RUNS_TO_COMPARE)
    assert _trend(session).comparison.direction is TrendDirection.FLAT


def test_model_and_stage_filters_and_their_option_lists(session):
    _run(session, weeks_ago=1, tokens=100, model="claude-opus-5", stage_key="implement")
    _run(session, weeks_ago=1, tokens=900, model="claude-sonnet-5", stage_key="review")
    _run(session, weeks_ago=1, tokens=50, model="Auto", stage_key="review")

    everything = _trend(session, weeks=2)
    assert everything.models == ["(unattributed)", "claude-opus-5", "claude-sonnet-5"]
    assert everything.stages == ["implement", "review"]

    sonnet = _trend(session, weeks=2, model="claude-sonnet-5")
    assert sonnet.weeks[0].median_tokens_per_run == 900
    # Each list ignores its own filter.
    assert len(sonnet.models) == 3
    assert sonnet.stages == ["review"]

    unattributed = _trend(session, weeks=2, model=UNATTRIBUTED_MODEL)
    assert unattributed.weeks[0].totals.runs == 1

    review = _trend(session, weeks=2, stage_key="review")
    assert review.weeks[0].totals.runs == 2
    assert review.models == ["(unattributed)", "claude-sonnet-5"]


def test_endpoint_serves_trend_with_nulls_kept(client):
    body = client.get("/api/runs/usage/trend", params={"weeks": 3}).json()
    assert len(body["weeks"]) == 3
    assert body["weeks"][0]["total_tokens"] is None
    assert body["comparison"]["direction"] == "unknown"
    assert body["comparison"]["change_percent"] is None
    assert body["metric"] == "total"
    uncached = client.get("/api/runs/usage/trend", params={"metric": "uncached"}).json()
    assert uncached["metric"] == "uncached"
    assert client.get("/api/runs/usage/trend", params={"weeks": 0}).status_code == 422
