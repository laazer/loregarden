"""The measurements a schedule forecast stands on, and how a date is judged.

A schedule is two dates per milestone: the **target** someone committed to
(`ScheduleTarget`) and the **forecast**, recomputed on every read by
`initiative_graph` from the plan's dependency graph. Nothing is stored, because
the answer changes every time a ticket closes — that is what makes the schedule
dynamic: finish work faster and every forecast behind it moves earlier; stall
and they slide.

Two measurements feed each ticket's duration, each answering what the other
cannot:

* **Pace** — work items resolved per day over a trailing window
  (`tickets.resolved_at`). It includes every wait a ticket really has: queue
  time, approvals, a person reading a diff. Measured on the initiative's own
  tickets per workspace when there are enough, else the whole workspace's, and
  the basis says which.
* **Agent run-time** — the remaining stages at their historical cost
  (`TicketTreeEstimator`). It ignores waits, so alone it reads optimistic; as a
  floor it stops a fast week from forecasting work sooner than its agents could
  physically run it.

A "work item" is any descendant that is not itself a milestone or initiative;
a milestone with no children counts as one item. Pace and remaining work count
the same unit, so the ratio between them means something.
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
    ScheduleMode,
    ScheduleStatus,
    ScheduleTarget,
    Ticket,
    WorkItemType,
    comparable_utc,
)
from loregarden.services.run_duration_stats import DurationStats, load_duration_stats
from sqlmodel import Session, col, func, select

#: Trailing window pace is measured over. Three weeks: long enough to hold a
#: few completions in a slow stretch (the live database ran 2-70 a week), short
#: enough that a change of pace shows within days.
WINDOW_DAYS = 21

#: Completions an initiative needs in a workspace, inside the window, before
#: its own pace is trusted over the workspace's.
MIN_INITIATIVE_SAMPLES = 5

#: Completions a workspace needs before its pace is trusted at all. Below it,
#: agent run-time prices the work: one ticket closed in a new workspace is not
#: a rate, and read as one it made closing work push the forecast *later*.
MIN_WORKSPACE_SAMPLES = 3

_CONTAINERS = frozenset({WorkItemType.INITIATIVE, WorkItemType.MILESTONE})

#: How long the run-time medians are reused. They are 90-day medians, so minutes
#: cannot move them; reading them costs ~1s of disk on the live database, which
#: every plan read and every target edit would otherwise pay.
STATS_TTL_SECONDS = 300.0

#: database URL -> (monotonic time read, stats). Keyed, so two databases in one
#: process (tests, a sandbox) never read each other's history.
_stats_cache: dict[str, tuple[float, DurationStats]] = {}
_stats_lock = threading.Lock()

#: Milestones with no plan row sort after every planned one.
_UNPLANNED_ORDER = 1_000_000


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


@dataclass(frozen=True)
class Pace:
    per_day: float | None
    completed: int
    basis: ForecastBasis


def _in_window(ticket: Ticket, since: datetime) -> bool:
    return ticket.resolved_at is not None and comparable_utc(ticket.resolved_at) >= since


def _window_days(earliest: datetime | None, now: datetime, since: datetime) -> float:
    """Days the window really covers: a workspace two days old has two days of pace."""
    if earliest is None:
        return float(WINDOW_DAYS)
    start = max(comparable_utc(earliest), since)
    return min(float(WINDOW_DAYS), max(1.0, (now - start).total_seconds() / 86_400))


def _workspace_completions(
    session: Session, workspace_ids: set[str], since: datetime
) -> dict[str, tuple[int, datetime | None]]:
    """Per workspace: work items resolved since `since`, and its oldest work item."""
    if not workspace_ids:
        return {}
    counted = col(Ticket.work_item_type).not_in(list(_CONTAINERS))
    resolved = dict(
        session.exec(
            select(Ticket.workspace_id, func.count())
            .where(
                col(Ticket.workspace_id).in_(workspace_ids),
                col(Ticket.resolved_at).is_not(None),
                col(Ticket.resolved_at) >= since,
                counted,
            )
            .group_by(col(Ticket.workspace_id))
        ).all()
    )
    oldest = dict(
        session.exec(
            select(Ticket.workspace_id, func.min(Ticket.created_at))
            .where(col(Ticket.workspace_id).in_(workspace_ids), counted)
            .group_by(col(Ticket.workspace_id))
        ).all()
    )
    return {
        workspace_id: (resolved.get(workspace_id, 0), oldest.get(workspace_id))
        for workspace_id in workspace_ids
    }


def measure_paces(
    session: Session, items_by_workspace: dict[str, list[Ticket]], now: datetime
) -> dict[str, Pace]:
    """Pace per workspace: the initiative's own when it has the samples.

    Each rate is over the days the work has existed inside the window, not the
    whole window: a workspace created last week has a week of pace.
    """
    since = now - timedelta(days=WINDOW_DAYS)
    fallback = _workspace_completions(session, set(items_by_workspace) - {""}, since)

    paces: dict[str, Pace] = {}
    for workspace_id, items in items_by_workspace.items():
        own = sum(1 for t in items if _in_window(t, since))
        if own >= MIN_INITIATIVE_SAMPLES:
            oldest = min((comparable_utc(t.created_at) for t in items), default=None)
            days = _window_days(oldest, now, since)
            paces[workspace_id] = Pace(own / days, own, ForecastBasis.INITIATIVE_THROUGHPUT)
            continue
        total, oldest_in_workspace = fallback.get(workspace_id, (0, None))
        if total >= MIN_WORKSPACE_SAMPLES:
            days = _window_days(oldest_in_workspace, now, since)
            paces[workspace_id] = Pace(total / days, total, ForecastBasis.WORKSPACE_THROUGHPUT)
        else:
            paces[workspace_id] = Pace(None, max(own, total), ForecastBasis.NONE)
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


def plan_sequence(milestones: list[Ticket], targets: dict[str, ScheduleTarget]) -> list[Ticket]:
    """The order milestones get lanes in: planned ones by order, then priority and age.

    One order across workspaces — milestones are phases of one plan, and a
    phase can span repositories.
    """

    def key(milestone: Ticket) -> tuple[int, int, datetime]:
        row = targets.get(milestone.id)
        plan_order = row.plan_order if row is not None else _UNPLANNED_ORDER
        return (plan_order, milestone.priority, comparable_utc(milestone.created_at))

    return sorted(milestones, key=key)


def group_by_workspace(tickets: list[Ticket]) -> dict[str, list[Ticket]]:
    groups: dict[str, list[Ticket]] = defaultdict(list)
    for ticket in tickets:
        groups[ticket.workspace_id or ""].append(ticket)
    return groups


def plan_mode(plan: InitiativePlan | None) -> ScheduleMode:
    return plan.mode if plan is not None else ScheduleMode.FIXED
