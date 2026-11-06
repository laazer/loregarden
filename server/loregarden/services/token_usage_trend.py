"""Whether agent runs are getting cheaper in tokens, week over week.

The per-run figures already exist (``agents.run_usage`` records them, and
``run_token_usage`` sums them per ticket). What nobody could answer was the
trend: is a run today spending less than a run a month ago?

Raw weekly totals cannot answer that. They rise with volume — October ran five
times the runs September did — so a week that spent more may have spent less
per run. The figure this module compares is the **median total tokens per
measured run**: robust to the one runaway implement round, and independent of
how many runs a week happened to dispatch.

The same rule as ``run_token_usage`` holds throughout: a run nobody measured is
not a run that spent nothing. Unmeasured runs are counted beside every figure
and never folded into a median, and a window with too few measured runs returns
no comparison rather than a confident percentage over a handful of rows.

Run mix still matters (an Opus week and a Sonnet week are different
populations), which is why the trend filters by model and stage: compare like
with like before concluding anything.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from enum import StrEnum
from statistics import median

from loregarden.models.domain import AgentRun, RunStatus
from loregarden.models.domain.enums import utcnow
from loregarden.services.run_token_usage import TokenTotals, is_costable_model, totals_for
from sqlalchemy.orm import load_only
from sqlmodel import Session, col, select

#: Weeks shown when the caller does not say. Usage was first recorded in late
#: August 2026; twelve weeks covers everything measured as of November.
DEFAULT_WEEKS = 12
MAX_WEEKS = 104

#: Complete weeks on each side of the comparison. One week is too noisy (a
#: single heavy ticket moves its median ~30% here); four smooths that out.
COMPARE_WEEKS = 4

#: Fewer measured runs than this on either side and the comparison is withheld.
MIN_RUNS_TO_COMPARE = 20

#: Changes smaller than this read as flat; week-to-week medians wobble ~5% on
#: unchanged prompts.
FLAT_BAND_PERCENT = 5.0

#: The group name for runs whose adapter named no priceable model, matching
#: ``run_token_usage.usage_by_model``.
UNATTRIBUTED_MODEL = "(unattributed)"

#: Only finished runs. An in-flight run has not printed its usage event yet and
#: would count as unmeasured when it is merely unfinished.
_FINISHED = (RunStatus.SUCCEEDED, RunStatus.FAILED, RunStatus.CANCELLED)

_QUEUE_SLACK = timedelta(days=7)


class TrendDirection(StrEnum):
    DOWN = "down"
    UP = "up"
    FLAT = "flat"
    #: Too few measured runs on one side to say.
    UNKNOWN = "unknown"


class TrendMetric(StrEnum):
    """What one run's tokens are summed as before taking the median."""

    #: Every token: input, output, cache reads and cache writes.
    TOTAL = "total"
    #: Everything but cache reads. Cache reads are ~95% of the total here and
    #: bill at a tenth of input, so they can swamp a change in fresh tokens.
    UNCACHED = "uncached"


@dataclass(frozen=True)
class TrendFilter:
    """Which runs the trend covers. ``None`` means no restriction."""

    weeks: int = DEFAULT_WEEKS
    metric: TrendMetric = TrendMetric.TOTAL
    workspace_id: str | None = None
    model: str | None = None
    stage_key: str | None = None


@dataclass(frozen=True)
class WeekUsage:
    week_start: date
    #: The week containing "now", still filling up. Excluded from the comparison.
    partial: bool
    totals: TokenTotals
    tickets: int
    #: Median of the chosen metric over this week's measured runs, or None.
    median_tokens_per_run: int | None


@dataclass(frozen=True)
class TrendComparison:
    recent_start: date
    prior_start: date
    recent_measured_runs: int
    prior_measured_runs: int
    recent_median: int | None
    prior_median: int | None
    #: Negative is cheaper. None when either side is too thin to compare.
    change_percent: float | None
    direction: TrendDirection


@dataclass(frozen=True)
class TokenTrend:
    weeks: list[WeekUsage]
    comparison: TrendComparison
    #: Models and stages present in the window, for the filter controls. Each
    #: list ignores its own filter, so choosing one model still offers the rest.
    models: list[str]
    stages: list[str]


def week_start_of(moment: datetime) -> date:
    """The Monday (UTC) of the ISO week containing ``moment``."""
    day = moment.date()
    return day - timedelta(days=day.weekday())


def model_group(run: AgentRun) -> str:
    name = (run.model or "").strip()
    return name if is_costable_model(name) else UNATTRIBUTED_MODEL


def _run_moment(run: AgentRun) -> datetime:
    return run.started_at or run.created_at


def run_tokens(run: AgentRun, metric: TrendMetric) -> int | None:
    """One run's tokens under ``metric``, or None if the run was not measured."""
    totals = totals_for([run])
    if totals.measured_runs == 0:
        return None
    if metric is TrendMetric.UNCACHED:
        parts = (totals.input_tokens, totals.output_tokens, totals.cache_write_tokens)
        return sum(part or 0 for part in parts)
    return totals.total_tokens


def _measured_totals(runs: list[AgentRun], metric: TrendMetric) -> list[int]:
    """Each measured run's tokens. Unmeasured runs contribute nothing."""
    values: list[int] = []
    for run in runs:
        value = run_tokens(run, metric)
        if value is not None:
            values.append(value)
    return values


def _median(values: list[int]) -> int | None:
    return round(median(values)) if values else None


def _load_window(session: Session, *, since: date, workspace_id: str | None) -> list[AgentRun]:
    """Finished runs started on or after ``since``, without their transcripts.

    ``load_only`` matters: ``stdout`` holds the raw stream-json transcript,
    megabytes a row, and this reads thousands of rows.
    """
    # Filtered on created_at (always set) but bucketed by started_at, which can
    # trail it by however long a run queued; the slack keeps a run queued over
    # the boundary from falling out. Rows bucketed before ``since`` are ignored.
    since_dt = datetime.combine(since, datetime.min.time()) - _QUEUE_SLACK
    conditions = [
        col(AgentRun.status).in_(_FINISHED),
        col(AgentRun.created_at) >= since_dt,
    ]
    if workspace_id is not None:
        conditions.append(AgentRun.workspace_id == workspace_id)
    stmt = (
        select(AgentRun)
        .where(*conditions)
        .options(
            load_only(
                AgentRun.ticket_id,
                AgentRun.stage_key,
                AgentRun.model,
                AgentRun.started_at,
                AgentRun.created_at,
                AgentRun.input_tokens,
                AgentRun.output_tokens,
                AgentRun.cache_read_tokens,
                AgentRun.cache_write_tokens,
            )
        )
    )
    return list(session.exec(stmt).all())


def _compare(
    by_week: dict[date, list[AgentRun]], current_week: date, metric: TrendMetric
) -> TrendComparison:
    """Pooled median of the last ``COMPARE_WEEKS`` complete weeks against the
    ``COMPARE_WEEKS`` before them.

    Pooled rather than a median of weekly medians, so a week with 9 runs does
    not weigh the same as one with 650.
    """
    recent_start = current_week - timedelta(weeks=COMPARE_WEEKS)
    prior_start = recent_start - timedelta(weeks=COMPARE_WEEKS)
    recent: list[AgentRun] = []
    prior: list[AgentRun] = []
    for week, runs in by_week.items():
        if recent_start <= week < current_week:
            recent.extend(runs)
        elif prior_start <= week < recent_start:
            prior.extend(runs)

    recent_values = _measured_totals(recent, metric)
    prior_values = _measured_totals(prior, metric)
    recent_median = _median(recent_values)
    prior_median = _median(prior_values)

    change: float | None = None
    direction = TrendDirection.UNKNOWN
    enough = min(len(recent_values), len(prior_values)) >= MIN_RUNS_TO_COMPARE
    if enough and recent_median is not None and prior_median:
        change = round((recent_median - prior_median) / prior_median * 100, 1)
        if abs(change) < FLAT_BAND_PERCENT:
            direction = TrendDirection.FLAT
        elif change < 0:
            direction = TrendDirection.DOWN
        else:
            direction = TrendDirection.UP

    return TrendComparison(
        recent_start=recent_start,
        prior_start=prior_start,
        recent_measured_runs=len(recent_values),
        prior_measured_runs=len(prior_values),
        recent_median=recent_median,
        prior_median=prior_median,
        change_percent=change,
        direction=direction,
    )


def token_trend(session: Session, flt: TrendFilter, *, now: datetime | None = None) -> TokenTrend:
    """Weekly token usage over ``flt.weeks`` weeks, oldest first, with every
    week present (an empty week is a row of zero runs, not a gap the chart
    silently closes)."""
    weeks = max(1, min(flt.weeks, MAX_WEEKS))
    current_week = week_start_of(now or utcnow())
    first_week = current_week - timedelta(weeks=weeks - 1)
    # The comparison may reach further back than the table shows.
    load_from = min(first_week, current_week - timedelta(weeks=2 * COMPARE_WEEKS))
    rows = _load_window(session, since=load_from, workspace_id=flt.workspace_id)

    models = sorted(
        {model_group(r) for r in rows if flt.stage_key is None or r.stage_key == flt.stage_key}
    )
    stages = sorted({r.stage_key for r in rows if flt.model is None or model_group(r) == flt.model})

    by_week: dict[date, list[AgentRun]] = defaultdict(list)
    for run in rows:
        if flt.model is not None and model_group(run) != flt.model:
            continue
        if flt.stage_key is not None and run.stage_key != flt.stage_key:
            continue
        by_week[week_start_of(_run_moment(run))].append(run)

    week_rows: list[WeekUsage] = []
    for offset in range(weeks):
        week = first_week + timedelta(weeks=offset)
        runs = by_week.get(week, [])
        week_rows.append(
            WeekUsage(
                week_start=week,
                partial=week == current_week,
                totals=totals_for(runs, key=week.isoformat()),
                tickets=len({r.ticket_id for r in runs if r.ticket_id}),
                median_tokens_per_run=_median(_measured_totals(runs, flt.metric)),
            )
        )

    return TokenTrend(
        weeks=week_rows,
        comparison=_compare(by_week, current_week, flt.metric),
        models=models,
        stages=stages,
    )
