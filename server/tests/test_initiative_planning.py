"""Initiative schedules: resolution stamps, forecasts, the plan, proposals and the planner."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any
from unittest.mock import patch

import pytest
from loregarden.mcp.tools import execute_tool, normalize_tool_arguments
from loregarden.models.domain import (
    ForecastBasis,
    InitiativePlanUpdate,
    ProposalSource,
    ProposalStatus,
    ScheduleMode,
    ScheduleProposal,
    ScheduleProposalCreate,
    ScheduleStatus,
    ScheduleTargetInput,
    Ticket,
    TicketState,
    WorkItemType,
    Workspace,
)
from loregarden.services.initiative_forecast import MIN_INITIATIVE_SAMPLES, WINDOW_DAYS
from loregarden.services.initiative_plan_service import (
    ScheduleValidationError,
    accept_proposal,
    plan_view,
    propose_schedule,
    update_plan,
)
from loregarden.services.initiative_planner_service import planner_workspace
from loregarden.services.ticket_service import TicketService
from loregarden.services.ticket_state_service import choose, derive
from loregarden.testing.factories import make_ticket
from sqlmodel import Session, select

NOW = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)
TODAY = NOW.date()


@dataclass(frozen=True)
class _Tree:
    seconds: float | None

    def projected_seconds(self, lanes: int) -> float | None:
        return self.seconds


class _FlatEstimator:
    """Every milestone costs the same agent time; patched in where the floor matters."""

    seconds: float | None = None

    def __init__(self, *_args: Any, **_kwargs: Any) -> None:
        pass

    def estimate(self, _ticket_id: str) -> _Tree:
        return _Tree(type(self).seconds)


@pytest.fixture(autouse=True)
def _no_agent_history():
    _FlatEstimator.seconds = None
    with patch("loregarden.services.initiative_forecast.TicketTreeEstimator", _FlatEstimator):
        yield


@pytest.fixture(name="workspaces")
def workspaces_fixture(db_session: Session) -> tuple[Workspace, Workspace]:
    here = db_session.exec(select(Workspace).where(Workspace.slug == "loregarden")).one()
    there = Workspace(
        slug="elsewhere",
        name="Elsewhere",
        repo_path="/tmp/elsewhere",
        workflow_template_id=here.workflow_template_id,
    )
    db_session.add(there)
    db_session.commit()
    return here, there


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


def _items(
    session: Session,
    workspace: Workspace,
    parent: Ticket,
    *,
    open_count: int = 0,
    done_days_ago: list[int] = (),
) -> None:
    for i in range(open_count):
        make_ticket(
            session,
            workspace_id=workspace.id,
            title=f"{parent.title} open {i}",
            parent_ticket_id=parent.id,
        )
    for i, days in enumerate(done_days_ago):
        make_ticket(
            session,
            workspace_id=workspace.id,
            title=f"{parent.title} done {i}",
            parent_ticket_id=parent.id,
            state=TicketState.DONE,
            resolved_at=NOW - timedelta(days=days),
        )


def _set_target(
    session: Session, initiative: Ticket, ticket: Ticket, when: date | None, order: int
):
    update_plan(
        session,
        initiative.id,
        InitiativePlanUpdate(
            targets=[ScheduleTargetInput(ticket_id=ticket.id, target_date=when, plan_order=order)]
        ),
        actor="test",
    )


def _by_id(view, ticket: Ticket):
    return next(m for m in view.milestones if m.id == ticket.id)


# --- resolved_at -------------------------------------------------------------


def test_resolved_at_is_stamped_on_close_and_cleared_on_reopen(db_session, workspaces):
    ticket = make_ticket(db_session, workspace_id=workspaces[0].id, title="t")
    assert ticket.resolved_at is None

    choose(db_session, ticket, TicketState.DONE, actor="test")
    stamped = ticket.resolved_at
    assert stamped is not None

    # done -> wont_do keeps the moment the work left the plan.
    choose(db_session, ticket, TicketState.WONT_DO, actor="test")
    assert ticket.resolved_at == stamped

    choose(db_session, ticket, TicketState.BACKLOG, actor="test")
    assert ticket.resolved_at is None


def test_derived_close_is_stamped_too(db_session, workspaces):
    """The whole reason for the column: a stage-derived close emits no event."""
    ticket = make_ticket(db_session, workspace_id=workspaces[0].id, title="t")
    derive(ticket, TicketState.DONE, actor="workflow")
    assert ticket.resolved_at is not None


# --- forecasts ---------------------------------------------------------------


def test_forecast_runs_in_plan_order_within_a_workspace(db_session, workspaces):
    here, _ = workspaces
    initiative = _initiative(db_session)
    first = _milestone(db_session, here, "First", initiative)
    second = _milestone(db_session, here, "Second", initiative)
    # 7 completions in the window -> 1/3 per day on the initiative's own pace.
    _items(db_session, here, first, open_count=3, done_days_ago=[1, 2, 3, 4, 5, 6, 7])
    _items(db_session, here, second, open_count=3)
    _set_target(db_session, initiative, second, None, 0)
    _set_target(db_session, initiative, first, None, 1)

    view = plan_view(db_session, initiative.id, now=NOW)

    per_day = 7 / WINDOW_DAYS
    assert [m.id for m in view.milestones] == [second.id, first.id]
    assert _by_id(view, second).forecast_date == (NOW + timedelta(days=3 / per_day)).date()
    # Second's three items come first; First waits behind them.
    assert _by_id(view, first).forecast_date == (NOW + timedelta(days=6 / per_day)).date()
    assert _by_id(view, first).basis == ForecastBasis.INITIATIVE_THROUGHPUT
    assert view.forecast_date == _by_id(view, first).forecast_date


def test_workspaces_proceed_in_parallel(db_session, workspaces):
    here, there = workspaces
    initiative = _initiative(db_session)
    a = _milestone(db_session, here, "A", initiative)
    b = _milestone(db_session, there, "B", initiative)
    _items(db_session, here, a, open_count=2, done_days_ago=[1] * MIN_INITIATIVE_SAMPLES)
    _items(db_session, there, b, open_count=2, done_days_ago=[1] * MIN_INITIATIVE_SAMPLES)

    view = plan_view(db_session, initiative.id, now=NOW)

    assert _by_id(view, a).forecast_date == _by_id(view, b).forecast_date
    assert {p.workspace_slug for p in view.paces} == {"loregarden", "elsewhere"}


def test_pace_falls_back_to_the_workspace_then_to_nothing(db_session, workspaces):
    here, there = workspaces
    initiative = _initiative(db_session)
    a = _milestone(db_session, here, "A", initiative)
    b = _milestone(db_session, there, "B", initiative)
    _items(db_session, here, a, open_count=1)
    _items(db_session, there, b, open_count=1)
    # Outside the initiative, but in its workspace: stands in for its pace.
    make_ticket(
        db_session,
        workspace_id=here.id,
        title="unrelated",
        state=TicketState.DONE,
        resolved_at=NOW - timedelta(days=2),
    )
    # Outside the window: counts for nothing.
    make_ticket(
        db_session,
        workspace_id=there.id,
        title="ancient",
        state=TicketState.DONE,
        resolved_at=NOW - timedelta(days=WINDOW_DAYS + 5),
    )

    view = plan_view(db_session, initiative.id, now=NOW)

    assert _by_id(view, a).basis == ForecastBasis.WORKSPACE_THROUGHPUT
    assert _by_id(view, a).forecast_date is not None
    assert _by_id(view, b).basis == ForecastBasis.NONE
    assert _by_id(view, b).forecast_date is None
    # One open milestone cannot be priced, so the initiative is not either —
    # the latest of the others would understate it.
    assert view.forecast_date is None
    assert view.unforecast_milestones == 1


def test_agent_time_floor_holds_a_fast_pace_back(db_session, workspaces):
    here, _ = workspaces
    initiative = _initiative(db_session)
    m = _milestone(db_session, here, "M", initiative)
    _items(db_session, here, m, open_count=1, done_days_ago=[1] * 20)
    _FlatEstimator.seconds = timedelta(days=10).total_seconds()

    view = plan_view(db_session, initiative.id, now=NOW)

    row = _by_id(view, m)
    assert row.basis == ForecastBasis.AGENT_TIME
    assert row.forecast_date == row.earliest_date == (NOW + timedelta(days=10)).date()


def test_status_against_target(db_session, workspaces):
    here, there = workspaces
    initiative = _initiative(db_session)
    ahead = _milestone(db_session, here, "Ahead", initiative)
    behind = _milestone(db_session, there, "Behind", initiative)
    late = _milestone(db_session, here, "Late", initiative)
    _items(db_session, here, ahead, open_count=1, done_days_ago=[1] * 21)
    _items(db_session, there, behind, open_count=21, done_days_ago=[1] * 21)
    _items(db_session, here, late, open_count=1)
    _set_target(db_session, initiative, ahead, TODAY + timedelta(days=30), 0)
    _set_target(db_session, initiative, behind, TODAY + timedelta(days=5), 0)
    _set_target(db_session, initiative, late, TODAY - timedelta(days=1), 1)

    view = plan_view(db_session, initiative.id, now=NOW)

    assert _by_id(view, ahead).status == ScheduleStatus.ON_TRACK
    assert _by_id(view, behind).status == ScheduleStatus.BEHIND
    assert _by_id(view, behind).drift_days == 21 - 5
    assert _by_id(view, late).status == ScheduleStatus.LATE


def test_rolling_mode_plans_on_the_forecast(db_session, workspaces):
    here, _ = workspaces
    initiative = _initiative(db_session)
    m = _milestone(db_session, here, "M", initiative)
    _items(db_session, here, m, open_count=7, done_days_ago=[1] * 7)
    target = TODAY + timedelta(days=5)
    _set_target(db_session, initiative, m, target, 0)

    fixed = _by_id(plan_view(db_session, initiative.id, now=NOW), m)
    assert fixed.planned_date == target

    update_plan(
        db_session, initiative.id, InitiativePlanUpdate(mode=ScheduleMode.ROLLING), actor="t"
    )
    rolling = _by_id(plan_view(db_session, initiative.id, now=NOW), m)
    assert rolling.planned_date == rolling.forecast_date != target
    # The baseline survives, so the drift is still visible.
    assert rolling.target_date == target


def test_finishing_work_moves_the_forecast_earlier(db_session, workspaces):
    """The dynamic part: nothing is re-planned, the next read just knows more."""
    here, _ = workspaces
    initiative = _initiative(db_session)
    m = _milestone(db_session, here, "M", initiative)
    _items(db_session, here, m, open_count=6, done_days_ago=[1] * MIN_INITIATIVE_SAMPLES)
    before = _by_id(plan_view(db_session, initiative.id, now=NOW), m).forecast_date

    open_items = db_session.exec(
        select(Ticket).where(Ticket.parent_ticket_id == m.id, Ticket.state == TicketState.BACKLOG)
    ).all()
    for ticket in open_items[:3]:
        choose(db_session, ticket, TicketState.DONE, actor="test")
        ticket.resolved_at = NOW - timedelta(hours=1)
    db_session.commit()

    after = _by_id(plan_view(db_session, initiative.id, now=NOW), m).forecast_date
    assert after < before


# --- plan edits and proposals ------------------------------------------------


def test_targets_may_only_name_the_initiative_and_its_milestones(db_session, workspaces):
    here, _ = workspaces
    initiative = _initiative(db_session)
    m = _milestone(db_session, here, "M", initiative)
    task = make_ticket(db_session, workspace_id=here.id, title="task", parent_ticket_id=m.id)

    with pytest.raises(ScheduleValidationError, match="Not a milestone"):
        update_plan(
            db_session,
            initiative.id,
            InitiativePlanUpdate(targets=[ScheduleTargetInput(ticket_id=task.id)]),
            actor="t",
        )
    # The initiative itself is fine.
    _set_target(db_session, initiative, initiative, TODAY, 0)
    assert plan_view(db_session, initiative.id, now=NOW).target_date == TODAY


def test_a_new_proposal_supersedes_and_accepting_applies(db_session, workspaces):
    here, _ = workspaces
    initiative = _initiative(db_session)
    m = _milestone(db_session, here, "M", initiative)
    first = propose_schedule(
        db_session,
        initiative.id,
        ScheduleProposalCreate(
            rationale="first", items=[ScheduleTargetInput(ticket_id=m.id, target_date=TODAY)]
        ),
        source=ProposalSource.DRAFT,
    )
    second = propose_schedule(
        db_session,
        initiative.id,
        ScheduleProposalCreate(
            rationale="second",
            items=[ScheduleTargetInput(ticket_id=m.id, target_date=TODAY + timedelta(days=9))],
            mode=ScheduleMode.ROLLING,
        ),
        source=ProposalSource.CHAT,
    )
    db_session.refresh(first)
    assert first.status == ProposalStatus.SUPERSEDED
    # Proposing wrote no target.
    view = plan_view(db_session, initiative.id, now=NOW)
    assert _by_id(view, m).target_date is None
    assert view.pending_proposal is not None and view.pending_proposal.id == second.id

    accepted = accept_proposal(db_session, initiative.id, second.id, actor="human")

    assert _by_id(accepted, m).target_date == TODAY + timedelta(days=9)
    assert accepted.mode == ScheduleMode.ROLLING
    assert accepted.notes == "second"
    assert accepted.pending_proposal is None
    with pytest.raises(ScheduleValidationError, match="already accepted"):
        accept_proposal(db_session, initiative.id, second.id, actor="human")


# --- API ---------------------------------------------------------------------


def test_plan_endpoints(client, db_session, workspaces):
    here, _ = workspaces
    initiative = _initiative(db_session)
    m = _milestone(db_session, here, "M", initiative)

    assert client.get(f"/api/initiatives/{m.id}/plan").status_code == 404

    res = client.patch(
        f"/api/initiatives/{initiative.id}/plan",
        json={"mode": "rolling", "targets": [{"ticket_id": m.id, "target_date": "2026-12-01"}]},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["mode"] == "rolling"
    assert body["milestones"][0]["target_date"] == "2026-12-01"

    bad = client.patch(
        f"/api/initiatives/{initiative.id}/plan",
        json={"targets": [{"ticket_id": "nope"}]},
    )
    assert bad.status_code == 400

    proposal = propose_schedule(
        db_session,
        initiative.id,
        ScheduleProposalCreate(rationale="r", items=[ScheduleTargetInput(ticket_id=m.id)]),
        source=ProposalSource.CHAT,
    )
    res = client.post(f"/api/initiatives/{initiative.id}/plan/proposals/{proposal.id}/discard")
    assert res.status_code == 200
    assert res.json()["pending_proposal"] is None
    again = client.post(f"/api/initiatives/{initiative.id}/plan/proposals/{proposal.id}/accept")
    assert again.status_code == 409


def test_board_reads_the_whole_subtree_across_workspaces(client, db_session, workspaces):
    here, there = workspaces
    initiative = _initiative(db_session)
    a = _milestone(db_session, here, "A", initiative)
    b = _milestone(db_session, there, "B", initiative)
    deep = make_ticket(db_session, workspace_id=there.id, title="deep", parent_ticket_id=b.id)
    make_ticket(db_session, workspace_id=here.id, title="outsider")

    res = client.get("/api/tickets", params={"ancestor_ticket_id": initiative.id})

    assert res.status_code == 200
    assert {t["id"] for t in res.json()} == {a.id, b.id, deep.id}


def test_planner_chat_round_trip(client, db_session, workspaces, monkeypatch):
    here, _ = workspaces
    initiative = _initiative(db_session)
    _milestone(db_session, here, "M", initiative)
    monkeypatch.setenv("LOREGARDEN_INITIATIVE_PLANNER_STUB_RESPONSE", "Proposed a schedule.")

    res = client.post(
        f"/api/initiatives/{initiative.id}/planner/messages", json={"content": "", "mode": "draft"}
    )

    assert res.status_code == 202, res.text
    snapshot = res.json()
    # LOREGARDEN_SYNC_RUNS settles the turn inside the request.
    assert snapshot["active_turn_id"] is None
    user, assistant = snapshot["messages"]
    assert user["role"] == "user" and user["content"]  # the draft request is filled in
    assert assistant["status"] == "complete"
    assert assistant["content"] == "Proposed a schedule."


def test_planner_needs_a_milestone_and_one_turn_at_a_time(client, db_session, workspaces):
    here, _ = workspaces
    initiative = _initiative(db_session)
    empty = client.post(
        f"/api/initiatives/{initiative.id}/planner/messages", json={"content": "hi"}
    )
    assert empty.status_code == 400
    assert "attach one" in empty.json()["detail"]

    _milestone(db_session, here, "M", initiative)
    with patch("loregarden.api.initiatives.schedule_planner_turn"):
        first = client.post(
            f"/api/initiatives/{initiative.id}/planner/messages", json={"content": "hi"}
        )
        assert first.status_code == 202
        assert first.json()["active_turn_id"] is not None
        busy = client.post(
            f"/api/initiatives/{initiative.id}/planner/messages", json={"content": "again"}
        )
        assert busy.status_code == 409

    stopped = client.post(f"/api/initiatives/{initiative.id}/planner/stop")
    assert stopped.status_code == 200
    assert stopped.json()["active_turn_id"] is None
    assert stopped.json()["messages"][-1]["status"] == "failed"


# --- MCP ---------------------------------------------------------------------


def _call(session: Session, name: str, args: dict[str, Any]) -> Any:
    return json.loads(execute_tool(session, name, normalize_tool_arguments(name, args)))


def test_mcp_tools_read_and_propose_by_external_id(db_session, workspaces):
    here, _ = workspaces
    initiative = _initiative(db_session)
    m = _milestone(db_session, here, "M", initiative)

    plan = _call(
        db_session, "loregarden_get_initiative_plan", {"initiative_id": initiative.external_id}
    )
    assert plan["id"] == initiative.id
    assert plan["milestones"][0]["status"] == "unscheduled"

    result = _call(
        db_session,
        "loregarden_propose_initiative_schedule",
        {
            "initiative_id": initiative.external_id,
            "rationale": "Because.",
            "items": [{"ticket_id": m.id, "target_date": "2026-11-15", "plan_order": 0}],
            "source": "draft",
        },
    )
    proposal = db_session.get(ScheduleProposal, result["proposal_id"])
    assert proposal is not None
    assert proposal.status == ProposalStatus.PENDING
    assert proposal.source == ProposalSource.DRAFT


def test_an_omitted_date_is_left_alone_and_null_clears_it(db_session, workspaces):
    """A reorder, or a proposal that only moves order, must not wipe the dates."""
    here, _ = workspaces
    initiative = _initiative(db_session)
    m = _milestone(db_session, here, "M", initiative)
    _set_target(db_session, initiative, m, TODAY, 0)

    update_plan(
        db_session,
        initiative.id,
        InitiativePlanUpdate(targets=[ScheduleTargetInput(ticket_id=m.id, plan_order=3)]),
        actor="t",
    )
    row = _by_id(plan_view(db_session, initiative.id, now=NOW), m)
    # Order is a position in its workspace: past the end lands at the end.
    assert (row.target_date, row.plan_order) == (TODAY, 0)

    proposal = propose_schedule(
        db_session,
        initiative.id,
        ScheduleProposalCreate(
            rationale="r", items=[ScheduleTargetInput(ticket_id=m.id, plan_order=0)]
        ),
        source=ProposalSource.CHAT,
    )
    view = plan_view(db_session, initiative.id, now=NOW)
    assert view.pending_proposal is not None
    assert view.pending_proposal.items == [{"ticket_id": m.id, "plan_order": 0}]
    accepted = accept_proposal(db_session, initiative.id, proposal.id, actor="human")
    assert _by_id(accepted, m).target_date == TODAY

    update_plan(
        db_session,
        initiative.id,
        InitiativePlanUpdate(targets=[ScheduleTargetInput(ticket_id=m.id, target_date=None)]),
        actor="t",
    )
    assert _by_id(plan_view(db_session, initiative.id, now=NOW), m).target_date is None


def test_dating_a_milestone_does_not_move_it(db_session, workspaces):
    """The first row written used to sort ahead of every unplanned milestone."""
    here, _ = workspaces
    initiative = _initiative(db_session)
    first = _milestone(db_session, here, "First", initiative)
    second = _milestone(db_session, here, "Second", initiative)
    third = _milestone(db_session, here, "Third", initiative)
    before = [m.id for m in plan_view(db_session, initiative.id, now=NOW).milestones]
    assert before == [first.id, second.id, third.id]

    update_plan(
        db_session,
        initiative.id,
        InitiativePlanUpdate(targets=[ScheduleTargetInput(ticket_id=third.id, target_date=TODAY)]),
        actor="t",
    )

    after = plan_view(db_session, initiative.id, now=NOW).milestones
    assert [m.id for m in after] == before
    assert [m.plan_order for m in after] == [0, 1, 2]


def test_workspace_groups_keep_their_place(db_session, workspaces):
    here, there = workspaces
    initiative = _initiative(db_session)
    a = _milestone(db_session, there, "A", initiative)  # "elsewhere" sorts first
    b = _milestone(db_session, here, "B", initiative)
    _set_target(db_session, initiative, b, TODAY, 0)

    view = plan_view(db_session, initiative.id, now=NOW)
    assert [m.id for m in view.milestones] == [a.id, b.id]


def test_planner_runs_in_a_workspace_that_is_checked_out(db_session, workspaces):
    """Live data: the first milestone's workspace was not on this machine."""
    here, there = workspaces  # "elsewhere" points at a path that does not exist
    initiative = _initiative(db_session)
    _milestone(db_session, there, "Away", initiative)
    with pytest.raises(ValueError, match="checked out"):
        planner_workspace(db_session, initiative)

    _milestone(db_session, here, "Home", initiative)
    assert planner_workspace(db_session, initiative).id == here.id
