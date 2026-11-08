"""`loregarden_settle_criterion`: only the stage that owns a disposition may record it.

An implementer that could mark its own criteria met would be grading its own
work, so met belongs to the verify stage (or, in a workflow with none, the AC
gate that settles criteria, as Blobert TDD's does) and delegated to the spec
stage, each on its own ticket. An operator may record either.
"""

from __future__ import annotations

import json

import pytest
from loregarden.mcp.tools import execute_tool, normalize_tool_arguments
from loregarden.models.domain import (
    AgentRun,
    CriterionDisposition,
    RunStatus,
    Ticket,
    WorkflowTemplate,
    Workspace,
)
from loregarden.services.acceptance_criteria import load_criteria, serialize_criteria
from loregarden.services.criterion_ledger import settlements_of, unsettled
from loregarden.services.seed import seed_database
from sqlmodel import Session, select
from tests.factories import make_workspace_ticket

CRITERION = "Errors name the arm"
TEST_ID = "server/tests/test_arms.py::test_names_the_arm"


@pytest.fixture(name="ticket")
def ticket_fixture(db_session: Session) -> Ticket:
    seed_database(db_session)
    ticket = make_workspace_ticket(db_session, "ct-ticket")
    ticket.acceptance_criteria_json = serialize_criteria([CRITERION])
    db_session.add(ticket)
    db_session.commit()
    return ticket


def _run(session: Session, ticket: Ticket, agent_id: str) -> AgentRun:
    run = AgentRun(
        run_code=f"{agent_id}-{ticket.external_id}",
        ticket_id=ticket.id,
        workspace_id=ticket.workspace_id,
        agent_id=agent_id,
        stage_key=agent_id,
        status=RunStatus.RUNNING,
    )
    session.add(run)
    session.commit()
    return run


def _settle(session: Session, run: AgentRun | None, **arguments) -> dict:
    return json.loads(
        execute_tool(
            session,
            "loregarden_settle_criterion",
            normalize_tool_arguments(
                "loregarden_settle_criterion", {"reason": "because", **arguments}
            ),
            orchestrated=run is not None,
            run_id=run.id if run else "",
        )
    )


def test_the_verify_stage_records_a_criterion_met(db_session, ticket):
    result = _settle(
        db_session,
        _run(db_session, ticket, "verifier"),
        ticket_id=ticket.id,
        criterion=CRITERION,
        disposition="met",
        tests=[TEST_ID],
    )
    assert result["disposition"] == "met" and result["settled_by"].startswith("run:")
    assert unsettled(db_session, ticket) == []


@pytest.mark.parametrize(
    ("agent", "disposition"),
    [("backend_implementer", "met"), ("spec", "met"), ("verifier", "delegated")],
)
def test_no_other_stage_may_record_it(db_session, ticket, agent, disposition):
    other = make_workspace_ticket(db_session, "ct-other")
    with pytest.raises(ValueError, match="recorded"):
        _settle(
            db_session,
            _run(db_session, ticket, agent),
            ticket_id=ticket.id,
            criterion=CRITERION,
            disposition=disposition,
            tests=[TEST_ID],
            target_ticket_id=other.id,
        )


def test_a_verifier_on_another_ticket_may_not_record_it(db_session, ticket):
    other = make_workspace_ticket(db_session, "ct-other")
    with pytest.raises(ValueError, match="recorded"):
        _settle(
            db_session,
            _run(db_session, other, "verifier"),
            ticket_id=ticket.id,
            criterion=CRITERION,
            disposition="met",
            tests=[TEST_ID],
        )


def test_the_spec_delegates_and_the_target_is_given_the_exact_text(db_session, ticket):
    target = make_workspace_ticket(db_session, "ct-target")
    _settle(
        db_session,
        _run(db_session, ticket, "spec"),
        ticket_id=ticket.id,
        criterion=CRITERION,
        disposition="delegated",
        target_ticket_id=target.external_id,
    )
    db_session.refresh(target)
    assert load_criteria(target.acceptance_criteria_json) == [CRITERION]
    row = settlements_of(db_session, ticket)[CRITERION]
    assert (
        row.disposition is CriterionDisposition.DELEGATED
        and row.delegated_to_ticket_id == target.id
    )


def test_an_operator_may_record_either(db_session, ticket):
    assert (
        _settle(
            db_session,
            None,
            ticket_id=ticket.id,
            criterion=CRITERION,
            disposition="met",
            tests=[TEST_ID],
        )["settled_by"]
        == "operator"
    )


@pytest.fixture(name="blobert_ticket")
def blobert_ticket_fixture(db_session: Session, ticket: Ticket) -> Ticket:
    """The same ticket on a Blobert-shaped workflow: no verify stage, an AC gate."""
    stages = [
        {"key": "implement", "name": "Implement", "agent_id": "core_simulation", "order": 1},
        {"key": "script_review", "name": "Review", "agent_id": "static_qa", "order": 2},
        {
            "key": "ac_gate",
            "name": "AC Gate",
            "agent_id": "ac_gatekeeper",
            "order": 3,
            "stage_type": "gate",
        },
        {"key": "done", "name": "Done", "order": 4, "terminal": True},
    ]
    template = WorkflowTemplate(
        slug="ct-blobert", name="Blobert shape", stages_json=json.dumps(stages)
    )
    db_session.add(template)
    db_session.flush()
    workspace = db_session.exec(select(Workspace).where(Workspace.id == ticket.workspace_id)).one()
    workspace.workflow_template_id = template.id
    db_session.add(workspace)
    db_session.commit()
    return ticket


def test_the_ac_gate_records_a_criterion_met_where_there_is_no_verify_stage(
    db_session, blobert_ticket
):
    result = _settle(
        db_session,
        _run(db_session, blobert_ticket, "ac_gatekeeper"),
        ticket_id=blobert_ticket.id,
        criterion=CRITERION,
        disposition="met",
        tests=[TEST_ID],
    )
    assert result["disposition"] == "met"
    assert unsettled(db_session, blobert_ticket) == []


@pytest.mark.parametrize("agent", ["core_simulation", "static_qa"])
def test_no_other_blobert_stage_may_record_it_met(db_session, blobert_ticket, agent):
    with pytest.raises(ValueError, match="recorded"):
        _settle(
            db_session,
            _run(db_session, blobert_ticket, agent),
            ticket_id=blobert_ticket.id,
            criterion=CRITERION,
            disposition="met",
            tests=[TEST_ID],
        )


def test_an_ac_gatekeeper_may_not_record_it_on_a_workflow_it_does_not_gate(db_session, ticket):
    with pytest.raises(ValueError, match="recorded"):
        _settle(
            db_session,
            _run(db_session, ticket, "ac_gatekeeper"),
            ticket_id=ticket.id,
            criterion=CRITERION,
            disposition="met",
            tests=[TEST_ID],
        )
