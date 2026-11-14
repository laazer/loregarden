"""The autopilot's clock: each minute, apply schedules, then tick every plan that is on.

A schedule turns a plan on (`AutopilotSchedule`): once at `start_at`, and each
time its weekly `window` opens. It is edge-triggered, not level-triggered:
each window occurrence is acted on once (`autopilot_window_seen`), so a person
who turns the autopilot off mid-window is not overruled a minute later, and a
plan that stopped itself (`paused_reason`) is not restarted by a schedule —
the circuit breaker asks a person to look first.

A window closes only a run it opened (`autopilot_window_owns`). One a person
started, or one already on when the window opened, runs on past the window.
Stops (`autopilot_timing`) end any run, scheduled or not, in the engine's tick.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime

from loregarden.config import settings
from loregarden.db.session import engine
from loregarden.models.domain import (
    AutopilotAction,
    AutopilotEvent,
    AutopilotUpdate,
    InitiativePlan,
    comparable_utc,
    utcnow,
)
from loregarden.services.autopilot_timing import load_schedule, save_schedule
from loregarden.services.initiative_autopilot import (
    AutopilotRefusedError,
    pause_reason_now,
    run_autopilot,
    set_autopilot,
    stop_autopilot,
)
from sqlmodel import Session, col, select

logger = logging.getLogger(__name__)

#: `autopilot_schedule_json` of a plan with no schedule.
_NO_SCHEDULE = "{}"


def _refuse(session: Session, plan: InitiativePlan, detail: str) -> None:
    session.add(
        AutopilotEvent(
            initiative_id=plan.initiative_id, action=AutopilotAction.REFUSED, detail=detail
        )
    )
    session.commit()


def _start(session: Session, plan: InitiativePlan, why: str) -> bool:
    """Turn it on for `why`, unless it is on already or stopped itself. True if it did."""
    if plan.autopilot:
        return False
    if plan.paused_reason:
        _refuse(
            session,
            plan,
            f"did not start for {why}: it stopped itself, and only a person turns it back "
            f"on ({pause_reason_now(session, plan)})",
        )
        return False
    try:
        set_autopilot(session, plan.initiative_id, AutopilotUpdate(enabled=True), actor=why)
    except AutopilotRefusedError as exc:
        logger.warning("Scheduled autopilot start refused for %s: %s", plan.initiative_id, exc)
        _refuse(session, plan, f"did not start for {why}: {exc}")
        return False
    return True


def run_schedule(session: Session, initiative_id: str, now: datetime) -> bool:
    """Apply one plan's schedule at `now`. True when it turned the plan on."""
    plan = session.get(InitiativePlan, initiative_id)
    if plan is None:
        return False
    schedule = load_schedule(plan)
    window = schedule.window
    seen = comparable_utc(plan.autopilot_window_seen) if plan.autopilot_window_seen else None
    span = window.occurrence_at(now) if window is not None else None

    # A run its window opened ends when that occurrence does.
    if plan.autopilot and plan.autopilot_window_owns and window is not None:
        if span is None or span[0] != seen:
            stop_autopilot(session, plan, f"its schedule window closed ({window.describe()})", now)

    started = False
    if schedule.start_at is not None and now >= schedule.start_at:
        # One-shot: cleared before starting, so a refused start is not retried
        # every minute (the refusal is in the log).
        schedule.start_at = None
        save_schedule(plan, schedule)
        session.add(plan)
        session.commit()
        started = _start(session, plan, "its scheduled start time")

    if window is not None and span is not None and span[0] != seen:
        plan.autopilot_window_seen = span[0]
        session.add(plan)
        session.commit()
        if _start(session, plan, f"its schedule window ({window.describe()})"):
            plan.autopilot_window_owns = True
            session.add(plan)
            session.commit()
            started = True
    return started


# ---- the clock -----------------------------------------------------------


def _scheduled_ids() -> list[str]:
    with Session(engine) as session:
        return list(
            session.exec(
                select(InitiativePlan.initiative_id).where(
                    col(InitiativePlan.autopilot_schedule_json) != _NO_SCHEDULE
                )
            ).all()
        )


def _enabled_ids() -> list[str]:
    with Session(engine) as session:
        return list(
            session.exec(
                select(InitiativePlan.initiative_id).where(col(InitiativePlan.autopilot).is_(True))
            ).all()
        )


def _tick() -> int:
    """Schedules first, so a plan they turn on is ticked this minute; then every
    plan with autopilot on, each in its own session. Returns tickets queued."""
    now = utcnow()
    for initiative_id in _scheduled_ids():
        try:
            with Session(engine) as session:
                run_schedule(session, initiative_id, now)
        except Exception:  # noqa: BLE001 — one plan's schedule must not stop the others
            logger.exception("Autopilot schedule failed for %s; continuing", initiative_id)
    total = 0
    for initiative_id in _enabled_ids():
        try:
            with Session(engine) as session:
                total += run_autopilot(session, initiative_id)
        except Exception:  # noqa: BLE001 — one initiative's failure must not stop the others
            logger.exception("Autopilot tick failed for initiative %s; continuing", initiative_id)
    return total


async def run_autopilot_loop(interval_seconds: float) -> None:
    while True:
        try:
            await asyncio.sleep(interval_seconds)
            await asyncio.to_thread(_tick)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 — the loop outliving a bad tick is the point
            logger.exception("Autopilot loop iteration failed; continuing")


def start_autopilot_loop(interval_seconds: float | None = None) -> asyncio.Task | None:
    """Start the loop, or None when the interval is non-positive (the off switch)."""
    if interval_seconds is None:
        interval_seconds = settings.autopilot_interval_seconds
    if interval_seconds <= 0:
        return None
    return asyncio.create_task(run_autopilot_loop(interval_seconds), name="initiative-autopilot")
