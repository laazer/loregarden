"""Read and write an autopilot's timer and schedule on its `InitiativePlan` row.

The models and their rules are `models.domain.autopilot_timing`; this module
is the row's side of them: parse the JSON columns, apply an update, evaluate
the stops, and say when it next starts or stops. No session, no commit: the
engine (`initiative_autopilot`) and the clock (`autopilot_schedule`) own those.
"""

from __future__ import annotations

from datetime import datetime

from loregarden.models.domain import (
    AutopilotSchedule,
    AutopilotStop,
    AutopilotUpdate,
    InitiativePlan,
    StopContext,
    comparable_utc,
)
from pydantic import TypeAdapter

_STOPS = TypeAdapter(list[AutopilotStop])


class AutopilotTimingError(ValueError):
    """A timer or schedule that cannot be saved, and why."""


def load_stops(plan: InitiativePlan) -> list[AutopilotStop]:
    return _STOPS.validate_json(plan.autopilot_stops_json)


def load_schedule(plan: InitiativePlan) -> AutopilotSchedule:
    return AutopilotSchedule.model_validate_json(plan.autopilot_schedule_json)


def save_stops(plan: InitiativePlan, stops: list[AutopilotStop]) -> None:
    plan.autopilot_stops_json = _STOPS.dump_json(stops).decode()


def save_schedule(plan: InitiativePlan, schedule: AutopilotSchedule) -> None:
    plan.autopilot_schedule_json = schedule.model_dump_json(exclude_none=True)


def _context(plan: InitiativePlan, now: datetime) -> StopContext:
    since = plan.autopilot_since
    return StopContext(now=now, since=comparable_utc(since) if since is not None else None)


def apply_timing(plan: InitiativePlan, update: AutopilotUpdate, now: datetime) -> None:
    """Store the update's stops and schedule; omitted halves stay as they are.

    A stop time or start time already past is refused rather than stored: the
    first would end the run the moment it starts, the second would start it
    at once, and neither is what the person picked.
    """
    if update.stops is not None:
        past = [s for s in update.stops if s.one_shot and s.reached(_context(plan, now))]
        if past:
            raise AutopilotTimingError("the stop time is already past; pick a later one")
        save_stops(plan, update.stops)
    if update.schedule is not None:
        start_at = update.schedule.start_at
        if start_at is not None and start_at <= now:
            raise AutopilotTimingError("the start time is already past; pick a later one")
        if update.schedule.window is None:
            # A window that is gone leaves nothing for its bookkeeping to mean.
            plan.autopilot_window_seen = None
            plan.autopilot_window_owns = False
        save_schedule(plan, update.schedule)


def stop_reason(plan: InitiativePlan, now: datetime) -> str | None:
    """Why the run should end now, or None. The first stop reached wins."""
    ctx = _context(plan, now)
    for stop in load_stops(plan):
        reason = stop.reached(ctx)
        if reason is not None:
            return reason
    return None


def drop_spent_stops(plan: InitiativePlan, now: datetime) -> list[str]:
    """Remove one-shot stops already reached; return why each one went.

    Called when a run ends on a stop, and when one starts: a deadline that
    passed while it was off would otherwise end the next run on its first tick.
    """
    ctx = _context(plan, now)
    kept: list[AutopilotStop] = []
    spent: list[str] = []
    for stop in load_stops(plan):
        reason = stop.reached(ctx) if stop.one_shot else None
        if reason is None:
            kept.append(stop)
        else:
            spent.append(reason)
    if spent:
        save_stops(plan, kept)
    return spent


def stops_at(plan: InitiativePlan, now: datetime) -> datetime | None:
    """While on: the earliest instant a time stop or its own window ends the run."""
    if not plan.autopilot:
        return None
    since = _context(plan, now).since
    ends = [end for stop in load_stops(plan) if (end := stop.stops_at(since)) is not None]
    window = load_schedule(plan).window
    if plan.autopilot_window_owns and window is not None:
        span = window.occurrence_at(now)
        if span is not None:
            ends.append(span[1])
    return min(ends) if ends else None


def next_start(plan: InitiativePlan, now: datetime) -> datetime | None:
    """While off: the next instant its schedule turns it on."""
    if plan.autopilot:
        return None
    return load_schedule(plan).next_start(now)
