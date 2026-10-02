"""An initiative as a dependency graph, and the autopilot that drives it."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from loregarden.mcp.tools import execute_tool, normalize_tool_arguments
from loregarden.models.domain import (
    AutopilotAction,
    AutopilotEvent,
    AutopilotUpdate,
    BlockKind,
    ForecastBasis,
    InitiativePlan,
    InitiativePlanUpdate,
    NodeStatus,
    ScheduleMode,
    ScheduleStatus,
    ScheduleTargetInput,
    Ticket,
    TicketDependency,
    TicketState,
    WorkItemType,
    Workspace,
)
from loregarden.services.dependency_readiness import UnmetPrerequisite, UnmetReason
from loregarden.services.initiative_autopilot import (
    BREAKER_LIMIT,
    mark_needs_person,
    run_autopilot,
    set_autopilot,
    start_ready_work,
)
from loregarden.services.initiative_graph import NEEDS_PERSON_TAG, lane_for
from loregarden.services.initiative_plan_service import plan_view, update_plan
from loregarden.services.ticket_service import TicketService
from loregarden.services.ticket_tags import load_tags, serialize_tags
from loregarden.testing.factories import make_ticket
from sqlmodel import Session, select

NOW = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)
DAY = 86_400.0


@dataclass(frozen=True)
class _Tree:
    seconds: float | None

    def projected_seconds(self, lanes: int) -> float | None:
        return self.seconds


class _Estimator:
    """Agent run-time per ticket: `per_ticket[id]`, else `default` (seconds)."""

    default: float | None = DAY
    per_ticket: dict[str, float | None] = {}

    def __init__(self, *_args: Any, **_kwargs: Any) -> None:
        pass

    def prime(self, _roots: list[Ticket]) -> None:
        pass

    def estimate(self, ticket_id: str) -> _Tree:
        return _Tree(type(self).per_ticket.get(ticket_id, type(self).default))


@pytest.fixture(autouse=True)
def _agent_time():
    _Estimator.default, _Estimator.per_ticket = DAY, {}
    with patch("loregarden.services.initiative_graph.TicketTreeEstimator", _Estimator):
        yield


@pytest.fixture(name="workspaces")
def workspaces_fixture(db_session: Session) -> tuple[Workspace, Workspace]:
    """Two workspaces with no history, so durations are the test's own.

    The seeded workspace has recent completions, and its measured pace would
    override every agent-time duration a test sets.
    """
    template = db_session.exec(select(Workspace).where(Workspace.slug == "loregarden")).one()
    made = []
    for slug in ("here", "elsewhere"):
        workspace = Workspace(
            slug=slug,
            name=slug.title(),
            repo_path=f"/tmp/{slug}",
            workflow_template_id=template.workflow_template_id,
        )
        db_session.add(workspace)
        made.append(workspace)
    db_session.commit()
    return made[0], made[1]


def _initiative(session: Session) -> Ticket:
    return TicketService(session).create_ticket(
        title="Ship it", work_item_type=WorkItemType.INITIATIVE
    )


def _milestone(session: Session, workspace: Workspace, title: str, parent: Ticket) -> Ticket:
    return TicketService(session).create_ticket(
        workspace_slug=workspace.slug,
        title=title,
        work_item_type=WorkItemType.MILESTONE,
        parent_ticket_id=parent.id,
    )


def _item(
    session: Session,
    workspace: Workspace,
    parent: Ticket,
    title: str,
    *,
    lane: str | None = None,
    tags: tuple[str, ...] = (),
    state: TicketState | None = None,
) -> Ticket:
    ticket = make_ticket(
        session,
        workspace_id=workspace.id,
        title=title,
        external_id=title,
        parent_ticket_id=parent.id,
        work_item_type=WorkItemType.FEATURE,
        state=state,
        resolved_at=NOW - timedelta(days=1) if state == TicketState.DONE else None,
    )
    all_tags = [*tags, *([f"tcg-lane-{lane}"] if lane else [])]
    if all_tags:
        ticket.tags_json = serialize_tags(all_tags)
        session.add(ticket)
        session.commit()
    return ticket


def _edge(session: Session, ticket: Ticket, waits_for: Ticket) -> None:
    session.add(TicketDependency(ticket_id=ticket.id, depends_on_ticket_id=waits_for.id))
    session.commit()


def _node(view, ticket: Ticket):
    return next(n for n in view.nodes if n.id == ticket.id)


def _day(days: float):
    return (NOW + timedelta(days=days)).date()


# --- the graph ---------------------------------------------------------------


def test_lanes_come_from_tags_else_the_workspace(db_session, workspaces):
    here, _ = workspaces
    initiative = _initiative(db_session)
    m = _milestone(db_session, here, "M", initiative)
    tagged = _item(db_session, here, m, "tagged", lane="model-render")
    plain = _item(db_session, here, m, "plain")
    assert lane_for(tagged, "here") == "model-render"
    assert lane_for(plain, "here") == "here"


def test_a_ticket_starts_when_its_prerequisites_finish(db_session, workspaces):
    here, there = workspaces
    initiative = _initiative(db_session)
    m = _milestone(db_session, here, "M", initiative)
    first = _item(db_session, here, m, "first", lane="a")
    second = _item(db_session, there, m, "second", lane="b")
    _edge(db_session, second, first)

    view = plan_view(db_session, initiative.id, now=NOW)

    assert _node(view, first).status == NodeStatus.READY
    assert _node(view, second).status == NodeStatus.WAITING
    assert _node(view, second).waiting_on == [first.id]
    assert _node(view, second).start == _node(view, first).finish
    assert view.milestones[0].forecast_date == _day(2)
    assert view.critical_path == [first.id, second.id]


def test_one_lane_runs_one_ticket_at_a_time(db_session, workspaces):
    here, _ = workspaces
    initiative = _initiative(db_session)
    m = _milestone(db_session, here, "M", initiative)
    a1 = _item(db_session, here, m, "a1", lane="a")
    a2 = _item(db_session, here, m, "a2", lane="a")
    b1 = _item(db_session, here, m, "b1", lane="b")

    view = plan_view(db_session, initiative.id, now=NOW)

    finishes = sorted(_node(view, t).finish for t in (a1, a2))
    assert finishes == [NOW + timedelta(days=1), NOW + timedelta(days=2)]
    assert _node(view, b1).finish == NOW + timedelta(days=1)


def test_the_earlier_phase_gets_a_shared_lane_first(db_session, workspaces):
    """A later phase must not book a lane ahead of an earlier phase's work."""
    here, _ = workspaces
    initiative = _initiative(db_session)
    slice_ = _milestone(db_session, here, "Slice", initiative)
    alpha = _milestone(db_session, here, "Alpha", initiative)
    later = _item(db_session, here, alpha, "alpha-work", lane="ops")
    sooner = _item(db_session, here, slice_, "slice-work", lane="ops")
    _Estimator.per_ticket = {later.id: 5 * DAY}  # the longer chain would win on rank alone
    update_plan(
        db_session,
        initiative.id,
        InitiativePlanUpdate(targets=[ScheduleTargetInput(ticket_id=slice_.id, plan_order=0)]),
        actor="t",
    )

    view = plan_view(db_session, initiative.id, now=NOW)

    assert _node(view, sooner).start == NOW
    assert _node(view, later).start == NOW + timedelta(days=1)


@pytest.mark.parametrize(
    "setup",
    [
        lambda t: setattr(t, "tags_json", serialize_tags([NEEDS_PERSON_TAG])),
        lambda t: setattr(t, "state", TicketState.PARKED),
        lambda t: (
            setattr(t, "state", TicketState.BLOCKED),
            setattr(t, "block_kind", BlockKind.DECISION),
        ),
    ],
    ids=["tagged", "parked", "decision"],
)
def test_work_for_a_person_is_never_picked(db_session, workspaces, setup):
    here, _ = workspaces
    initiative = _initiative(db_session)
    m = _milestone(db_session, here, "M", initiative)
    person = _item(db_session, here, m, "decide")
    setup(person)
    db_session.add(person)
    db_session.commit()

    view = plan_view(db_session, initiative.id, now=NOW)

    assert _node(view, person).status == NodeStatus.NEEDS_PERSON
    assert person.id not in view.autopilot.next_up


def test_an_outside_prerequisite_is_scheduled_in_its_own_lane(db_session, workspaces):
    here, there = workspaces
    initiative = _initiative(db_session)
    m = _milestone(db_session, here, "M", initiative)
    mine = _item(db_session, here, m, "mine")
    theirs = make_ticket(db_session, workspace_id=there.id, title="theirs", external_id="theirs")
    _edge(db_session, mine, theirs)

    view = plan_view(db_session, initiative.id, now=NOW)

    outside = _node(view, theirs)
    assert outside.external and outside.lane == "outside:elsewhere"
    assert outside.milestone_id is None
    assert theirs.id not in view.autopilot.next_up
    assert _node(view, mine).start == outside.finish


def test_no_measurement_anywhere_means_no_date(db_session, workspaces):
    here, _ = workspaces
    _Estimator.default = None
    initiative = _initiative(db_session)
    m = _milestone(db_session, here, "M", initiative)
    _item(db_session, here, m, "x")

    view = plan_view(db_session, initiative.id, now=NOW)

    assert view.milestones[0].forecast_date is None
    assert view.unforecast_milestones == 1
    assert view.forecast_date is None


def test_an_unmeasured_ticket_borrows_the_median_and_says_so(db_session, workspaces):
    """The fallback for a workspace with no history — the blank-schedule case."""
    here, _ = workspaces
    initiative = _initiative(db_session)
    m = _milestone(db_session, here, "M", initiative)
    measured = _item(db_session, here, m, "measured", lane="a")
    unmeasured = _item(db_session, here, m, "unmeasured", lane="b")
    _Estimator.per_ticket = {measured.id: 2 * DAY, unmeasured.id: None}

    view = plan_view(db_session, initiative.id, now=NOW)

    node = _node(view, unmeasured)
    assert node.assumed and node.duration_days == 2
    assert view.milestones[0].assumed == 1
    assert view.milestones[0].forecast_date == _day(2)


def test_pace_sets_duration_when_it_is_slower_than_agent_time(db_session, workspaces):
    here, _ = workspaces
    initiative = _initiative(db_session)
    m = _milestone(db_session, here, "M", initiative)
    item = _item(db_session, here, m, "open")
    for i in range(7):  # 7 in 21 days: one every three days, in one lane
        _item(db_session, here, m, f"done-{i}", state=TicketState.DONE)
    # Three weeks of history: pace is measured over the days the work existed.
    for ticket in db_session.exec(select(Ticket).where(Ticket.parent_ticket_id == m.id)).all():
        ticket.created_at = NOW - timedelta(days=30)
        db_session.add(ticket)
    db_session.commit()
    _Estimator.default = 3600.0

    view = plan_view(db_session, initiative.id, now=NOW)

    node = _node(view, item)
    assert node.basis == ForecastBasis.INITIATIVE_THROUGHPUT
    assert node.duration_days == pytest.approx(3.0)


def test_a_cycle_is_reported_not_looped_on(db_session, workspaces):
    here, _ = workspaces
    initiative = _initiative(db_session)
    m = _milestone(db_session, here, "M", initiative)
    a = _item(db_session, here, m, "a")
    b = _item(db_session, here, m, "b")
    _edge(db_session, a, b)
    _edge(db_session, b, a)  # written directly: the service would refuse it

    view = plan_view(db_session, initiative.id, now=NOW)

    assert set(view.cyclic) == {a.id, b.id}
    assert view.milestones[0].forecast_date is None


def test_closing_work_moves_the_forecast_earlier(db_session, workspaces):
    here, _ = workspaces
    initiative = _initiative(db_session)
    m = _milestone(db_session, here, "M", initiative)
    first = _item(db_session, here, m, "first")
    second = _item(db_session, here, m, "second")
    _edge(db_session, second, first)
    before = plan_view(db_session, initiative.id, now=NOW).milestones[0].forecast_date

    first.state, first.resolved_at = TicketState.DONE, NOW
    db_session.add(first)
    db_session.commit()

    after = plan_view(db_session, initiative.id, now=NOW).milestones[0].forecast_date
    assert after < before


def test_rolling_mode_plans_on_the_forecast(db_session, workspaces):
    here, _ = workspaces
    initiative = _initiative(db_session)
    m = _milestone(db_session, here, "M", initiative)
    _item(db_session, here, m, "x")
    target = _day(10)
    update_plan(
        db_session,
        initiative.id,
        InitiativePlanUpdate(
            mode=ScheduleMode.ROLLING,
            targets=[ScheduleTargetInput(ticket_id=m.id, target_date=target)],
        ),
        actor="t",
    )

    row = plan_view(db_session, initiative.id, now=NOW).milestones[0]
    assert row.planned_date == row.forecast_date == _day(1)
    assert row.target_date == target
    assert row.status == ScheduleStatus.ON_TRACK


# --- autopilot ---------------------------------------------------------------


@pytest.fixture(name="queue")
def queue_fixture():
    """The queue and the git readiness check, replaced: no real runs, no real repos."""
    lanes = MagicMock()
    lanes.lane_numbers.return_value = [1, 2, 3]
    lanes.waiting_in_lane.return_value = []
    lanes.add_to_lane.return_value = {"status": "started"}
    with (
        patch("loregarden.services.initiative_autopilot.QueueLaneService", return_value=lanes),
        patch(
            "loregarden.services.initiative_autopilot.unmet_prerequisites_for_start",
            return_value=[],
        ) as unmet,
    ):
        yield lanes, unmet


def _queued(lanes: MagicMock) -> list[str]:
    return [c.kwargs["ticket_id"] for c in lanes.add_to_lane.call_args_list]


def test_autopilot_does_nothing_until_turned_on(db_session, workspaces, queue):
    here, _ = workspaces
    initiative = _initiative(db_session)
    m = _milestone(db_session, here, "M", initiative)
    _item(db_session, here, m, "x")
    assert run_autopilot(db_session, initiative.id, now=NOW) == 0
    assert queue[0].add_to_lane.call_count == 0


def test_autopilot_fills_lanes_critical_path_first(db_session, workspaces, queue):
    lanes, _ = queue
    here, _ = workspaces
    initiative = _initiative(db_session)
    m = _milestone(db_session, here, "M", initiative)
    head = _item(db_session, here, m, "head", lane="a")
    tail = _item(db_session, here, m, "tail", lane="b")
    _edge(db_session, tail, head)
    side_a = _item(db_session, here, m, "side-a", lane="a")
    side_c = _item(db_session, here, m, "side-c", lane="c")
    person = _item(db_session, here, m, "decide", lane="d", tags=(NEEDS_PERSON_TAG,))
    set_autopilot(
        db_session, initiative.id, AutopilotUpdate(enabled=True, max_parallel=2), actor="t"
    )

    queued = run_autopilot(db_session, initiative.id, now=NOW)

    assert queued == 2
    order = _queued(lanes)
    assert order[0] == head.id  # on the critical path
    assert side_a.id not in order  # its lane is taken by head
    assert person.id not in order and tail.id not in order
    assert order[1] == side_c.id
    events = db_session.exec(
        select(AutopilotEvent).where(AutopilotEvent.action == AutopilotAction.DISPATCHED)
    ).all()
    assert {e.ticket_id for e in events} == set(order)
    assert all(c.kwargs["auto_approve"] is False for c in lanes.add_to_lane.call_args_list)


def test_unlanded_prerequisite_work_holds_a_ticket_and_says_so_once(db_session, workspaces, queue):
    lanes, unmet = queue
    here, _ = workspaces
    initiative = _initiative(db_session)
    m = _milestone(db_session, here, "M", initiative)
    item = _item(db_session, here, m, "x")
    blocker = make_ticket(db_session, workspace_id=here.id, title="b", external_id="lg-b-9")
    unmet.return_value = [UnmetPrerequisite(blocker, UnmetReason.NOT_LANDED, "integration/x")]
    set_autopilot(db_session, initiative.id, AutopilotUpdate(enabled=True), actor="t")

    run_autopilot(db_session, initiative.id, now=NOW)
    run_autopilot(db_session, initiative.id, now=NOW)

    assert lanes.add_to_lane.call_count == 0
    refused = db_session.exec(
        select(AutopilotEvent).where(
            AutopilotEvent.action == AutopilotAction.REFUSED, AutopilotEvent.ticket_id == item.id
        )
    ).all()
    assert len(refused) == 1
    assert "lg-b-9 (not_landed on integration/x)" in refused[0].detail


def test_autopilot_stops_itself_when_its_work_keeps_blocking(db_session, workspaces, queue):
    here, _ = workspaces
    initiative = _initiative(db_session)
    m = _milestone(db_session, here, "M", initiative)
    set_autopilot(db_session, initiative.id, AutopilotUpdate(enabled=True), actor="t")
    for i in range(BREAKER_LIMIT):
        t = _item(db_session, here, m, f"broken-{i}", state=TicketState.BLOCKED)
        db_session.add(
            AutopilotEvent(
                initiative_id=initiative.id, action=AutopilotAction.DISPATCHED, ticket_id=t.id
            )
        )
    db_session.commit()

    assert run_autopilot(db_session, initiative.id, now=NOW) == 0

    plan = db_session.get(InitiativePlan, initiative.id)
    assert plan is not None and not plan.autopilot
    assert "blocked" in plan.paused_reason
    view = plan_view(db_session, initiative.id, now=NOW)
    assert view.autopilot.paused_reason == plan.paused_reason
    assert view.autopilot.recent[0].action == AutopilotAction.PAUSED


def test_starting_named_work_refuses_what_is_not_ready(db_session, workspaces, queue):
    lanes, _ = queue
    here, _ = workspaces
    initiative = _initiative(db_session)
    m = _milestone(db_session, here, "M", initiative)
    ready = _item(db_session, here, m, "ready")
    waiting = _item(db_session, here, m, "waiting")
    _edge(db_session, waiting, ready)

    outcome = start_ready_work(db_session, initiative.id, [ready.id, waiting.id, "nope"], actor="t")

    assert outcome[ready.id] == "queued"
    assert outcome[waiting.id].startswith("not ready: waiting (waiting on ready)")
    assert outcome["nope"] == "not part of this initiative's plan"
    assert _queued(lanes) == [ready.id]


def test_marking_work_for_a_person(db_session, workspaces):
    here, _ = workspaces
    initiative = _initiative(db_session)
    m = _milestone(db_session, here, "M", initiative)
    item = _item(db_session, here, m, "decide", lane="a")

    assert mark_needs_person(db_session, initiative.id, [item.id], needs_person=True) == ["decide"]
    db_session.refresh(item)
    assert set(load_tags(item.tags_json)) == {NEEDS_PERSON_TAG, "tcg-lane-a"}
    assert mark_needs_person(db_session, initiative.id, [item.id], needs_person=True) == []
    with pytest.raises(ValueError, match="Not in this initiative"):
        mark_needs_person(db_session, initiative.id, ["nope"], needs_person=True)


def test_autopilot_endpoint_queues_the_first_batch(client, db_session, workspaces, queue):
    lanes, _ = queue
    here, _ = workspaces
    initiative = _initiative(db_session)
    m = _milestone(db_session, here, "M", initiative)
    item = _item(db_session, here, m, "x")

    res = client.patch(
        f"/api/initiatives/{initiative.id}/autopilot", json={"enabled": True, "max_parallel": 2}
    )

    assert res.status_code == 200, res.text
    body = res.json()
    assert body["autopilot"]["enabled"] is True and body["autopilot"]["max_parallel"] == 2
    assert _queued(lanes) == [item.id]
    assert (
        client.patch(
            f"/api/initiatives/{initiative.id}/autopilot", json={"max_parallel": 99}
        ).status_code
        == 422
    )


def _call(session: Session, name: str, args: dict[str, Any]) -> Any:
    return json.loads(execute_tool(session, name, normalize_tool_arguments(name, args)))


def test_the_planner_can_drive_through_mcp(db_session, workspaces, queue):
    lanes, _ = queue
    here, _ = workspaces
    initiative = _initiative(db_session)
    m = _milestone(db_session, here, "M", initiative)
    decide = _item(db_session, here, m, "decide", lane="a")
    build = _item(db_session, here, m, "build", lane="b")

    marked = _call(
        db_session,
        "loregarden_mark_needs_person",
        {"initiative_id": initiative.external_id, "ticket_ids": [decide.id]},
    )
    assert marked == {"changed": ["decide"], "needs_person": True}
    result = _call(
        db_session,
        "loregarden_set_initiative_autopilot",
        {"initiative_id": initiative.external_id, "enabled": True},
    )
    assert result == {"enabled": True, "max_parallel": 3, "queued_now": 1}
    assert _queued(lanes) == [build.id]


def test_a_young_workspace_is_paced_over_its_own_age(db_session, workspaces):
    """Three closed in a workspace three days old is one a day, not one a week."""
    here, _ = workspaces
    initiative = _initiative(db_session)
    m = _milestone(db_session, here, "M", initiative)
    item = _item(db_session, here, m, "open")
    for i in range(3):
        _item(db_session, here, m, f"done-{i}", state=TicketState.DONE)
    for ticket in db_session.exec(select(Ticket).where(Ticket.parent_ticket_id == m.id)).all():
        ticket.created_at = NOW - timedelta(days=3)
        db_session.add(ticket)
    db_session.commit()
    _Estimator.default = 60.0

    node = _node(plan_view(db_session, initiative.id, now=NOW), item)
    assert node.basis == ForecastBasis.WORKSPACE_THROUGHPUT
    assert node.duration_days == pytest.approx(1.0)


def test_a_sandbox_never_starts_runs(db_session, workspaces, queue):
    """A sandbox's database is a copy; the repositories a run would touch are not."""
    lanes, _ = queue
    here, _ = workspaces
    initiative = _initiative(db_session)
    m = _milestone(db_session, here, "M", initiative)
    item = _item(db_session, here, m, "x")

    with patch("loregarden.services.initiative_autopilot.settings.sandbox", True):
        outcome = start_ready_work(db_session, initiative.id, [item.id], actor="t")

    assert lanes.add_to_lane.call_count == 0
    assert outcome[item.id].startswith("refused")
