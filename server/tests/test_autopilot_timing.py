"""The autopilot's timer (stops) and schedule (one-off start, weekly window).

Models: `models.domain.autopilot_timing`. Row side: `services.autopilot_timing`.
Engine: `initiative_autopilot.run_autopilot` checks the stops; the clock
(`autopilot_schedule.run_schedule`) turns plans on and closes the windows it
opened.
"""

from __future__ import annotations

from datetime import UTC, datetime, time, timedelta

import pytest
from loregarden.models.domain import (
    AutopilotAction,
    AutopilotEvent,
    AutopilotSchedule,
    AutopilotUpdate,
    AutopilotWindow,
    DeadlineStop,
    InitiativePlan,
    RunForStop,
    StopContext,
    Ticket,
    WorkItemType,
    comparable_utc,
    utcnow,
)
from loregarden.services.autopilot_schedule import _tick, run_schedule
from loregarden.services.autopilot_timing import load_schedule, load_stops
from loregarden.services.initiative_autopilot import (
    AutopilotRefusedError,
    run_autopilot,
    set_autopilot,
)
from loregarden.services.subtree_autopilot import subtree_autopilot_view
from loregarden.services.ticket_service import TicketService
from pydantic import ValidationError
from sqlmodel import Session, col, select
from tests.test_initiative_graph import (  # noqa: F401 - fixtures are used by name
    _item,
    _queued,
    queue_fixture,
    workspaces_fixture,
)

#: A Tuesday, 23:00 UTC.
TUE_2300 = datetime(2026, 11, 10, 23, 0, tzinfo=UTC)

OVERNIGHT_WEEKDAYS = AutopilotWindow(
    days=[0, 1, 2, 3, 4], start=time(22, 0), end=time(6, 0), timezone="UTC"
)
EVERY_NIGHT = AutopilotWindow(
    days=list(range(7)), start=time(22, 0), end=time(6, 0), timezone="UTC"
)


# ---- the models ----------------------------------------------------------


def test_an_overnight_window_belongs_to_the_day_it_opens():
    opens = datetime(2026, 11, 10, 22, 0, tzinfo=UTC)
    assert OVERNIGHT_WEEKDAYS.occurrence_at(TUE_2300) == (opens, opens + timedelta(hours=8))
    # Saturday 03:00 is still Friday night's window.
    sat_0300 = datetime(2026, 11, 14, 3, 0, tzinfo=UTC)
    assert OVERNIGHT_WEEKDAYS.occurrence_at(sat_0300) is not None
    # Saturday 23:00 opens on a Saturday, which the window does not.
    assert OVERNIGHT_WEEKDAYS.occurrence_at(datetime(2026, 11, 14, 23, 0, tzinfo=UTC)) is None
    # Closing is exclusive.
    assert OVERNIGHT_WEEKDAYS.occurrence_at(datetime(2026, 11, 11, 6, 0, tzinfo=UTC)) is None


def test_a_window_keeps_its_wall_clock_hours_across_dst():
    window = AutopilotWindow(
        days=list(range(7)), start=time(22, 0), end=time(23, 0), timezone="America/New_York"
    )
    # EDT (UTC-4) on Oct 30, EST (UTC-5) on Nov 2.
    assert window.next_open(datetime(2026, 10, 30, 12, tzinfo=UTC)) == datetime(
        2026, 10, 31, 2, tzinfo=UTC
    )
    assert window.next_open(datetime(2026, 11, 2, 12, tzinfo=UTC)) == datetime(
        2026, 11, 3, 3, tzinfo=UTC
    )


def test_next_open_skips_days_the_window_does_not_open_on():
    fri_2300 = datetime(2026, 11, 13, 23, 0, tzinfo=UTC)
    assert OVERNIGHT_WEEKDAYS.next_open(fri_2300) == datetime(2026, 11, 16, 22, 0, tzinfo=UTC)


@pytest.mark.parametrize(
    "fields",
    [
        {"start": time(9), "end": time(9)},
        {"timezone": "Mars/Olympus_Mons"},
        {"days": [7]},
        {"days": []},
    ],
)
def test_a_window_that_cannot_happen_is_refused(fields):
    base = {"days": [0], "start": time(9), "end": time(17), "timezone": "UTC"}
    with pytest.raises(ValidationError):
        AutopilotWindow.model_validate(base | fields)


def test_a_deadline_without_a_zone_is_refused():
    with pytest.raises(ValidationError):
        DeadlineStop.model_validate({"kind": "deadline", "at": "2026-11-10T07:00:00"})


def test_run_for_counts_from_the_switch_on():
    stop = RunForStop(minutes=90)
    since = TUE_2300
    assert stop.reached(StopContext(now=since + timedelta(minutes=89), since=since)) is None
    assert stop.reached(StopContext(now=since + timedelta(minutes=90), since=since))
    assert stop.reached(StopContext(now=since, since=None)) is None


# ---- the engine ----------------------------------------------------------


def _parent(session: Session, workspace) -> Ticket:
    m = TicketService(session).create_ticket(
        workspace_slug=workspace.slug, title="Timed", work_item_type=WorkItemType.MILESTONE
    )
    _item(session, workspace, m, "child")
    return m


def _plan(session: Session, root: Ticket) -> InitiativePlan:
    plan = session.get(InitiativePlan, root.id)
    assert plan is not None
    session.refresh(plan)
    return plan


def _actions(session: Session, root: Ticket) -> list[tuple[AutopilotAction, str]]:
    rows = session.exec(
        select(AutopilotEvent)
        .where(AutopilotEvent.initiative_id == root.id)
        .order_by(col(AutopilotEvent.created_at))
    ).all()
    return [(e.action, e.detail) for e in rows]


def test_a_deadline_ends_the_run_and_is_spent(db_session, workspaces, queue):
    root = _parent(db_session, workspaces[0])
    at = utcnow() + timedelta(hours=1)
    set_autopilot(
        db_session, root.id, AutopilotUpdate(enabled=True, stops=[DeadlineStop(at=at)]), actor="t"
    )

    assert run_autopilot(db_session, root.id, now=at + timedelta(minutes=1)) == 0

    plan = _plan(db_session, root)
    assert not plan.autopilot
    assert plan.paused_reason == ""  # a timer is not a fault
    assert load_stops(plan) == []
    assert _actions(db_session, root)[-1][0] == AutopilotAction.STOPPED


def test_a_deadline_already_past_is_refused(db_session, workspaces, queue):
    root = _parent(db_session, workspaces[0])
    past = DeadlineStop(at=utcnow() - timedelta(minutes=1))
    with pytest.raises(AutopilotRefusedError, match="already past"):
        set_autopilot(db_session, root.id, AutopilotUpdate(enabled=True, stops=[past]), actor="t")


def test_run_for_is_kept_for_the_next_run(db_session, workspaces, queue):
    root = _parent(db_session, workspaces[0])
    set_autopilot(
        db_session,
        root.id,
        AutopilotUpdate(enabled=True, stops=[RunForStop(minutes=30)]),
        actor="t",
    )
    since = comparable_utc(_plan(db_session, root).autopilot_since)

    run_autopilot(db_session, root.id, now=since + timedelta(minutes=31))

    plan = _plan(db_session, root)
    assert not plan.autopilot
    assert load_stops(plan) == [RunForStop(minutes=30)]


def test_the_view_says_when_it_stops_and_when_it_starts(db_session, workspaces, queue):
    root = _parent(db_session, workspaces[0])
    start = utcnow() + timedelta(hours=2)
    set_autopilot(
        db_session,
        root.id,
        AutopilotUpdate(schedule=AutopilotSchedule(start_at=start)),
        actor="t",
    )
    view = subtree_autopilot_view(db_session, root.id).autopilot
    assert view.next_start == start
    assert view.stops_at is None

    set_autopilot(
        db_session,
        root.id,
        AutopilotUpdate(enabled=True, stops=[RunForStop(minutes=60)]),
        actor="t",
    )
    view = subtree_autopilot_view(db_session, root.id).autopilot
    since = comparable_utc(_plan(db_session, root).autopilot_since)
    assert view.stops_at == since + timedelta(minutes=60)
    assert view.next_start is None


# ---- the schedule --------------------------------------------------------


def test_a_one_off_start_turns_it_on_once(db_session, workspaces, queue):
    root = _parent(db_session, workspaces[0])
    start = utcnow() + timedelta(hours=1)
    set_autopilot(
        db_session, root.id, AutopilotUpdate(schedule=AutopilotSchedule(start_at=start)), actor="t"
    )

    assert not run_schedule(db_session, root.id, start - timedelta(minutes=1))
    assert run_schedule(db_session, root.id, start)

    plan = _plan(db_session, root)
    assert plan.autopilot
    assert load_schedule(plan).empty
    assert AutopilotAction.ENABLED in [a for a, _ in _actions(db_session, root)]


def test_a_start_time_already_past_is_refused(db_session, workspaces, queue):
    root = _parent(db_session, workspaces[0])
    past = AutopilotSchedule(start_at=utcnow() - timedelta(minutes=1))
    with pytest.raises(AutopilotRefusedError, match="already past"):
        set_autopilot(db_session, root.id, AutopilotUpdate(schedule=past), actor="t")


def _windowed(session: Session, root: Ticket, window: AutopilotWindow) -> None:
    set_autopilot(
        session, root.id, AutopilotUpdate(schedule=AutopilotSchedule(window=window)), actor="t"
    )


def test_a_window_turns_it_on_and_closes_the_run_it_opened(db_session, workspaces, queue):
    root = _parent(db_session, workspaces[0])
    _windowed(db_session, root, OVERNIGHT_WEEKDAYS)

    assert run_schedule(db_session, root.id, TUE_2300)
    plan = _plan(db_session, root)
    assert plan.autopilot and plan.autopilot_window_owns

    run_schedule(db_session, root.id, TUE_2300 + timedelta(hours=6))  # 05:00, still open
    assert _plan(db_session, root).autopilot

    run_schedule(db_session, root.id, TUE_2300 + timedelta(hours=8))  # 07:00, closed
    plan = _plan(db_session, root)
    assert not plan.autopilot
    assert not plan.autopilot_window_owns
    assert _actions(db_session, root)[-1][0] == AutopilotAction.STOPPED


def test_turning_it_off_mid_window_is_not_overruled(db_session, workspaces, queue):
    root = _parent(db_session, workspaces[0])
    _windowed(db_session, root, OVERNIGHT_WEEKDAYS)
    run_schedule(db_session, root.id, TUE_2300)
    set_autopilot(db_session, root.id, AutopilotUpdate(enabled=False), actor="t")

    assert not run_schedule(db_session, root.id, TUE_2300 + timedelta(minutes=1))
    assert not _plan(db_session, root).autopilot
    # The next night is a new occurrence.
    assert run_schedule(db_session, root.id, TUE_2300 + timedelta(days=1))


def test_a_window_never_ends_a_run_a_person_started(db_session, workspaces, queue):
    root = _parent(db_session, workspaces[0])
    _windowed(db_session, root, OVERNIGHT_WEEKDAYS)
    set_autopilot(db_session, root.id, AutopilotUpdate(enabled=True), actor="t")

    assert not run_schedule(db_session, root.id, TUE_2300)
    run_schedule(db_session, root.id, TUE_2300 + timedelta(hours=8))

    assert _plan(db_session, root).autopilot


def test_a_plan_that_stopped_itself_is_not_restarted_by_its_schedule(db_session, workspaces, queue):
    root = _parent(db_session, workspaces[0])
    _windowed(db_session, root, EVERY_NIGHT)
    plan = _plan(db_session, root)
    plan.paused_reason = "3 tickets it started are blocked"
    db_session.add(plan)
    db_session.commit()

    assert not run_schedule(db_session, root.id, TUE_2300)

    assert not _plan(db_session, root).autopilot
    assert _actions(db_session, root)[-1][0] == AutopilotAction.REFUSED
    # Once per occurrence, not every minute.
    run_schedule(db_session, root.id, TUE_2300 + timedelta(minutes=1))
    assert len([a for a, _ in _actions(db_session, root) if a == AutopilotAction.REFUSED]) == 1


def test_clearing_the_schedule_forgets_its_window(db_session, workspaces, queue):
    root = _parent(db_session, workspaces[0])
    _windowed(db_session, root, EVERY_NIGHT)
    run_schedule(db_session, root.id, TUE_2300)
    set_autopilot(db_session, root.id, AutopilotUpdate(schedule=AutopilotSchedule()), actor="t")

    plan = _plan(db_session, root)
    assert plan.autopilot_schedule_json == "{}"
    assert plan.autopilot_window_seen is None
    assert not plan.autopilot_window_owns


def test_the_clock_starts_a_scheduled_plan_and_queues_its_work_in_one_tick(
    db_session, workspaces, queue
):
    lanes, _ = queue
    root = _parent(db_session, workspaces[0])
    now = utcnow()
    open_now = AutopilotWindow(
        days=list(range(7)),
        start=(now - timedelta(hours=1)).time().replace(microsecond=0),
        end=(now + timedelta(hours=1)).time().replace(microsecond=0),
        timezone="UTC",
    )
    _windowed(db_session, root, open_now)

    assert _tick() == 1

    assert _plan(db_session, root).autopilot
    assert len(_queued(lanes)) == 1
