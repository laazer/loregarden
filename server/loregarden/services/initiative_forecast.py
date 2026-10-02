"""When an initiative's milestones will land, at the pace work is actually finishing.

A schedule is two dates per milestone: the **target** someone committed to
(`ScheduleTarget`) and the **forecast** recomputed here on every read. Nothing
is stored, because the answer changes every time a ticket closes — that is what
makes the schedule dynamic: finish work faster and every forecast behind it
moves earlier; stall and they slide.

The forecast blends two measurements, each answering what the other cannot:

* **Throughput** — work items resolved per day over a trailing window
  (`tickets.resolved_at`). It includes every wait a ticket really has: queue
  time, approvals, a person reading a diff. Measured on the initiative's own
  subtree per workspace when it has enough history, else the whole
  workspace's pace, and the basis says which.
* **Agent run-time** — the remaining stages at their historical cost, spread
  over the lanes (`TicketTreeEstimator`). It ignores waits, so alone it reads
  optimistic; as a *floor* it stops a fast week from forecasting a milestone
  sooner than its agents could physically run it.

Milestones are forecast **in plan order within each workspace**: a workspace
works through its share of the initiative sequentially, while different
workspaces proceed in parallel. A milestone's forecast is therefore the time to
clear its own work plus everything planned before it in the same workspace.

A "work item" is any descendant that is not itself a milestone or initiative;
a milestone with no children counts as one item. Remaining work and throughput
count the same unit, so the ratio between them means something.
"""

from __future__ import annotations

import threading
import time
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from loregarden.models.domain import (
    ForecastBasis,
    InitiativePlan,
    MilestoneSchedule,
    ScheduleMode,
    ScheduleStatus,
    ScheduleTarget,
    Ticket,
    WorkItemType,
    WorkspacePace,
    comparable_utc,
    utcnow,
)
from loregarden.services.hierarchy_service import descendants_by_root
from loregarden.services.run_duration_stats import DurationStats, load_duration_stats
from loregarden.services.ticket_state_service import RESOLVED_STATES
from loregarden.services.ticket_tree_estimate import TicketTreeEstimator
from sqlmodel import Session, col, func, select

#: Trailing window pace is measured over. Three weeks: long enough to hold a
#: few completions in a slow stretch (the live database ran 2-70 a week), short
#: enough that a change of pace shows within days.
WINDOW_DAYS = 21

#: Completions an initiative needs in a workspace, inside the window, before
#: its own pace is trusted over the workspace's.
MIN_INITIATIVE_SAMPLES = 5

#: Parallel lanes the run-time floor spreads work over. The queue's default.
DEFAULT_LANES = 3

_CONTAINERS = frozenset({WorkItemType.INITIATIVE, WorkItemType.MILESTONE})

#: How long the run-time medians are reused. They are 90-day medians, so minutes
#: cannot move them; reading them costs ~1s of disk on the live database, which
#: every plan read and every target edit would otherwise pay.
STATS_TTL_SECONDS = 300.0

#: database URL -> (monotonic time read, stats). Keyed, so two databases in one
#: process (tests, a sandbox) never read each other's history.
_stats_cache: dict[str, tuple[float, DurationStats]] = {}
_stats_lock = threading.Lock()


def duration_stats(session: Session) -> DurationStats:
    key = str(session.get_bind().url)
    now = time.monotonic()
    with _stats_lock:
        hit = _stats_cache.get(key)
        if hit is not None and now - hit[0] < STATS_TTL_SECONDS:
            return hit[1]
    stats = load_duration_stats(session)
    with _stats_lock:
        _stats_cache[key] = (now, stats)
    return stats


#: Milestones with no plan row sort after every planned one, then by priority.
_UNPLANNED_ORDER = 1_000_000


@dataclass(frozen=True)
class Pace:
    per_day: float | None
    completed: int
    basis: ForecastBasis


@dataclass(frozen=True)
class MilestoneWork:
    """The unit counts under one milestone."""

    milestone: Ticket
    items: list[Ticket]

    @property
    def remaining(self) -> list[Ticket]:
        return [t for t in self.items if t.state not in RESOLVED_STATES]


def milestone_work(milestone: Ticket, descendants: list[Ticket]) -> MilestoneWork:
    items = [t for t in descendants if t.work_item_type not in _CONTAINERS]
    return MilestoneWork(milestone=milestone, items=items or [milestone])


def _in_window(ticket: Ticket, since: datetime) -> bool:
    return ticket.resolved_at is not None and comparable_utc(ticket.resolved_at) >= since


def _workspace_completions(
    session: Session, workspace_ids: set[str], since: datetime
) -> dict[str, int]:
    if not workspace_ids:
        return {}
    rows = session.exec(
        select(Ticket.workspace_id, func.count())
        .where(
            col(Ticket.workspace_id).in_(workspace_ids),
            col(Ticket.resolved_at).is_not(None),
            col(Ticket.resolved_at) >= since,
            col(Ticket.work_item_type).not_in(list(_CONTAINERS)),
        )
        .group_by(col(Ticket.workspace_id))
    ).all()
    return {workspace_id: count for workspace_id, count in rows if workspace_id}


def measure_paces(session: Session, work: list[MilestoneWork], now: datetime) -> dict[str, Pace]:
    """Pace per workspace: the initiative's own when it has the samples."""
    since = now - timedelta(days=WINDOW_DAYS)
    own: dict[str, int] = defaultdict(int)
    workspaces: set[str] = set()
    for entry in work:
        workspace_id = entry.milestone.workspace_id or ""
        workspaces.add(workspace_id)
        own[workspace_id] += sum(1 for t in entry.items if _in_window(t, since))
    fallback = _workspace_completions(session, workspaces - {""}, since)

    paces: dict[str, Pace] = {}
    for workspace_id in workspaces:
        if own[workspace_id] >= MIN_INITIATIVE_SAMPLES:
            paces[workspace_id] = Pace(
                own[workspace_id] / WINDOW_DAYS,
                own[workspace_id],
                ForecastBasis.INITIATIVE_THROUGHPUT,
            )
        elif fallback.get(workspace_id):
            count = fallback[workspace_id]
            paces[workspace_id] = Pace(
                count / WINDOW_DAYS, count, ForecastBasis.WORKSPACE_THROUGHPUT
            )
        else:
            paces[workspace_id] = Pace(None, own[workspace_id], ForecastBasis.NONE)
    return paces


def classify(
    *, resolved: bool, target: date | None, forecast: date | None, today: date
) -> ScheduleStatus:
    if resolved:
        return ScheduleStatus.DONE
    if target is None:
        return ScheduleStatus.UNSCHEDULED
    if target < today:
        return ScheduleStatus.LATE
    if forecast is None:
        return ScheduleStatus.NO_FORECAST
    return ScheduleStatus.ON_TRACK if forecast <= target else ScheduleStatus.BEHIND


def planned(mode: ScheduleMode, target: date | None, forecast: date | None) -> date | None:
    if mode == ScheduleMode.ROLLING and forecast is not None:
        return forecast
    return target


def drift(target: date | None, forecast: date | None) -> int | None:
    if target is None or forecast is None:
        return None
    return (forecast - target).days


def plan_sequence(
    milestones: list[Ticket], targets: dict[str, ScheduleTarget], slugs: dict[str, str]
) -> list[tuple[str, list[Ticket]]]:
    """The order work proceeds in: per workspace (by slug, so groups never
    reshuffle), planned milestones by their order, then the rest by priority
    and age."""

    def key(milestone: Ticket) -> tuple[int, int, datetime]:
        row = targets.get(milestone.id)
        plan_order = row.plan_order if row is not None else _UNPLANNED_ORDER
        return (plan_order, milestone.priority, comparable_utc(milestone.created_at))

    groups: dict[str, list[Ticket]] = defaultdict(list)
    for milestone in sorted(milestones, key=key):
        groups[milestone.workspace_id or ""].append(milestone)
    return sorted(groups.items(), key=lambda kv: slugs.get(kv[0], kv[0]))


@dataclass(frozen=True)
class ForecastResult:
    milestones: list[MilestoneSchedule]
    paces: list[WorkspacePace]
    #: Latest open-milestone forecast; null when any open milestone has none.
    forecast_date: date | None
    unforecast_milestones: int


class InitiativeForecaster:
    """Forecasts one initiative's milestones against one read of history."""

    def __init__(
        self,
        session: Session,
        *,
        now: datetime | None = None,
        lanes: int = DEFAULT_LANES,
    ) -> None:
        self.session = session
        self.now = now or utcnow()
        self.lanes = lanes
        self.window_days = WINDOW_DAYS

    def forecast(
        self,
        milestones: list[Ticket],
        slugs: dict[str, str],
        targets: dict[str, ScheduleTarget],
        mode: ScheduleMode,
    ) -> ForecastResult:
        trees = descendants_by_root(self.session, [m.id for m in milestones])
        work = [milestone_work(m, trees[m.id]) for m in milestones]
        paces = measure_paces(self.session, work, self.now)
        estimator = TicketTreeEstimator(
            self.session, stats=duration_stats(self.session), now=self.now
        )
        today = self.now.date()

        work_by_id = {entry.milestone.id: entry for entry in work}
        by_workspace = {
            workspace_id: [work_by_id[m.id] for m in sequence]
            for workspace_id, sequence in plan_sequence(milestones, targets, slugs)
        }

        views: dict[str, MilestoneSchedule] = {}
        for workspace_id, entries in by_workspace.items():
            pace = paces[workspace_id]
            backlog = 0
            floor_seconds = 0.0
            for position, entry in enumerate(entries):
                views[entry.milestone.id] = self._schedule(
                    entry,
                    slug=slugs.get(workspace_id, ""),
                    position=position,
                    target=targets.get(entry.milestone.id),
                    pace=pace,
                    backlog_before=backlog,
                    floor_before=floor_seconds,
                    estimator=estimator,
                    mode=mode,
                    today=today,
                )
                if entry.milestone.state not in RESOLVED_STATES:
                    backlog += len(entry.remaining)
                    projected = estimator.estimate(entry.milestone.id).projected_seconds(self.lanes)
                    floor_seconds += projected or 0.0

        ordered = [
            views[entry.milestone.id] for entries in by_workspace.values() for entry in entries
        ]
        open_views = [v for v in ordered if v.status != ScheduleStatus.DONE]
        unforecast = sum(1 for v in open_views if v.forecast_date is None)
        latest = max((v.forecast_date for v in open_views if v.forecast_date), default=None)
        if not open_views:
            latest = today
        return ForecastResult(
            milestones=ordered,
            paces=[
                WorkspacePace(
                    workspace_slug=slugs.get(workspace_id, ""),
                    per_day=pace.per_day,
                    completed=pace.completed,
                    basis=pace.basis,
                )
                for workspace_id, pace in sorted(paces.items(), key=lambda kv: slugs.get(kv[0], ""))
            ],
            forecast_date=None if unforecast else latest,
            unforecast_milestones=unforecast,
        )

    def _schedule(
        self,
        entry: MilestoneWork,
        *,
        slug: str,
        position: int,
        target: ScheduleTarget | None,
        pace: Pace,
        backlog_before: int,
        floor_before: float,
        estimator: TicketTreeEstimator,
        mode: ScheduleMode,
        today: date,
    ) -> MilestoneSchedule:
        milestone = entry.milestone
        resolved = milestone.state in RESOLVED_STATES
        remaining = len(entry.remaining)
        target_date = target.target_date if target is not None else None

        forecast: date | None = None
        earliest: date | None = None
        basis = pace.basis
        if not resolved:
            projected = estimator.estimate(milestone.id).projected_seconds(self.lanes)
            floor_seconds = floor_before + (projected or 0.0)
            floor_at = self.now + timedelta(seconds=floor_seconds)
            earliest = floor_at.date() if projected is not None else None
            if remaining == 0:
                # Every item is closed and the rollup has not caught up yet.
                forecast = today
            elif pace.per_day:
                paced_at = self.now + timedelta(days=(backlog_before + remaining) / pace.per_day)
                if floor_at > paced_at:
                    paced_at, basis = floor_at, ForecastBasis.AGENT_TIME
                forecast = paced_at.date()

        return MilestoneSchedule(
            id=milestone.id,
            external_id=milestone.external_id,
            title=milestone.title,
            state=milestone.state,
            workspace_slug=slug,
            plan_order=position,
            target_date=target_date,
            forecast_date=forecast,
            earliest_date=earliest,
            planned_date=planned(mode, target_date, forecast),
            drift_days=None if resolved else drift(target_date, forecast),
            status=classify(resolved=resolved, target=target_date, forecast=forecast, today=today),
            basis=basis,
            remaining=remaining,
            total=len(entry.items),
        )


def plan_mode(plan: InitiativePlan | None) -> ScheduleMode:
    return plan.mode if plan is not None else ScheduleMode.FIXED
