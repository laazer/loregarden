"""What a ticket has left, counting the children that run with it.

A lane runs a whole ticket, and a ticket with children is not finished until
they are. The behaviours pinned here are the ones that were wrong when a lane
was priced at one agent's median: stages already behind the ticket are free,
pending stages cost more than one attempt because they re-run, and a subtree's
work and its critical path are different numbers.
"""

import json
from datetime import datetime, timedelta, timezone

import pytest
from loregarden.models.domain import (
    AgentRun,
    RunStatus,
    StageStatus,
    Ticket,
    TicketState,
    WorkflowInstance,
    WorkflowTemplate,
    Workspace,
)
from loregarden.services.run_duration_stats import DurationStats
from loregarden.services.ticket_tree_estimate import TicketTreeEstimator
from sqlmodel import Session

STAGES = [
    {"key": "spec", "name": "Spec", "agent_id": "spec", "order": 1},
    {"key": "implement", "name": "Implement", "agent_id": "backend_implementer", "order": 2},
    {"key": "verify", "name": "Verify", "agent_id": "verifier", "order": 3},
    {"key": "done", "name": "Done", "agent_id": "", "order": 4, "terminal": True},
]

#: One attempt each, no rework, so the arithmetic in the assertions is the
#: thing under test rather than the multiplier.
FLAT_STATS = DurationStats(
    by_agent={"*": 100.0},
    by_stage={"spec": 100.0, "implement": 400.0, "verify": 200.0},
    attempts_per_stage=1.0,
)


@pytest.fixture(name="session")
def session_fixture(isolated_db):
    with Session(isolated_db) as session:
        yield session


@pytest.fixture(name="workspace")
def workspace_fixture(session):
    template = WorkflowTemplate(
        slug="tdd",
        name="TDD",
        version=1,
        stages_json=json.dumps(STAGES),
        transitions_json="[]",
    )
    session.add(template)
    session.commit()
    session.refresh(template)

    ws = Workspace(slug="proj", name="proj", repo_path=".", workflow_template_id=template.id)
    session.add(ws)
    session.commit()
    session.refresh(ws)
    return ws


def _ticket(
    session: Session,
    workspace: Workspace,
    code: str,
    *,
    parent_id: str | None = None,
    state: TicketState = TicketState.BACKLOG,
    done_stages: tuple[str, ...] = (),
    running_stage: str = "",
) -> Ticket:
    ticket = Ticket(
        external_id=code,
        workspace_id=workspace.id,
        title=code,
        state=state,
        parent_ticket_id=parent_id,
    )
    session.add(ticket)
    session.commit()
    session.refresh(ticket)

    stages_json = json.dumps(
        [
            {
                "key": stage["key"],
                "status": (
                    StageStatus.DONE.value
                    if stage["key"] in done_stages
                    else StageStatus.RUNNING.value
                    if stage["key"] == running_stage
                    else StageStatus.PENDING.value
                ),
            }
            for stage in STAGES
        ]
    )
    session.add(
        WorkflowInstance(
            ticket_id=ticket.id,
            template_id=workspace.workflow_template_id,
            template_version=1,
            current_stage_key=running_stage or "spec",
            stages_json=stages_json,
        )
    )
    session.commit()
    return ticket


def _estimator(session) -> TicketTreeEstimator:
    return TicketTreeEstimator(session, stats=FLAT_STATS)


def test_a_fresh_ticket_costs_its_whole_pipeline(session, workspace):
    ticket = _ticket(session, workspace, "T-1")

    estimate = _estimator(session).estimate(ticket.id)

    # 100 + 400 + 200; the terminal stage has no agent and no history, so it
    # is free rather than priced at the workspace median.
    assert estimate.own_seconds == 700.0
    assert estimate.stage_count == 3
    assert estimate.unknown_tickets == 0


def test_finished_stages_are_not_charged_again(session, workspace):
    ticket = _ticket(session, workspace, "T-2", done_stages=("spec", "implement"))

    assert _estimator(session).estimate(ticket.id).own_seconds == 200.0


def test_a_running_stage_is_charged_only_for_what_is_left(session, workspace):
    ticket = _ticket(session, workspace, "T-3", done_stages=("spec",), running_stage="implement")
    session.add(
        AgentRun(
            run_code="r-1",
            ticket_id=ticket.id,
            workspace_id=workspace.id,
            agent_id="backend_implementer",
            stage_key="implement",
            status=RunStatus.RUNNING,
            started_at=datetime.now(timezone.utc) - timedelta(seconds=150),
        )
    )
    session.commit()

    # 400 - 150 elapsed, plus verify.
    assert _estimator(session).estimate(ticket.id).own_seconds == pytest.approx(450.0, abs=2)


def test_a_done_ticket_costs_nothing(session, workspace):
    ticket = _ticket(session, workspace, "T-4", state=TicketState.DONE)

    estimate = _estimator(session).estimate(ticket.id)

    assert estimate.own_seconds == 0.0
    assert estimate.ticket_count == 0


def test_children_are_counted_and_the_two_bounds_differ(session, workspace):
    """The whole reason a lane could not be priced by its own stages: the
    children run too, and summing them is not the same as finishing them."""
    parent = _ticket(session, workspace, "F-1")
    _ticket(session, workspace, "T-a", parent_id=parent.id)
    _ticket(session, workspace, "T-b", parent_id=parent.id)

    estimate = _estimator(session).estimate(parent.id)

    assert estimate.work_seconds == 2100.0  # three tickets of 700
    # One parent plus the deepest single child, which more lanes cannot shorten.
    assert estimate.critical_path_seconds == 1400.0
    assert estimate.ticket_count == 3
    # Two lanes cannot beat the chain; one lane cannot beat the total.
    assert estimate.projected_seconds(3) == 1400.0
    assert estimate.projected_seconds(1) == 2100.0


def test_finished_children_drop_out_of_the_subtree(session, workspace):
    parent = _ticket(session, workspace, "F-2")
    _ticket(session, workspace, "T-c", parent_id=parent.id, state=TicketState.DONE)

    estimate = _estimator(session).estimate(parent.id)

    assert estimate.work_seconds == 700.0
    assert estimate.ticket_count == 1


def test_rework_makes_pending_stages_cost_more_than_one_attempt(session, workspace):
    """History says a stage runs about 1.4 times. Costing it at one is how a
    whole-ticket estimate came in at two thirds of reality."""
    ticket = _ticket(session, workspace, "T-5")
    stats = DurationStats(
        by_agent=FLAT_STATS.by_agent,
        by_stage=FLAT_STATS.by_stage,
        attempts_per_stage=1.5,
    )

    estimate = TicketTreeEstimator(session, stats=stats).estimate(ticket.id)

    assert estimate.own_seconds == 1050.0


def test_no_history_estimates_nothing(session, workspace):
    ticket = _ticket(session, workspace, "T-6")

    estimate = TicketTreeEstimator(session, stats=DurationStats()).estimate(ticket.id)

    assert estimate.own_seconds is None
    assert estimate.work_seconds is None
    assert estimate.projected_seconds(3) is None
    assert estimate.unknown_tickets == 1


def test_a_parent_cycle_does_not_recurse_forever(session, workspace):
    """A bad parent link is a data bug; hanging the queue websocket on it would
    be a worse one."""
    a = _ticket(session, workspace, "T-7")
    b = _ticket(session, workspace, "T-8", parent_id=a.id)
    a.parent_ticket_id = b.id
    session.add(a)
    session.commit()

    assert _estimator(session).estimate(a.id).work_seconds is not None
