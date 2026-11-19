"""A settling stage's pass is refused while a criterion is unsettled, for the live agent to settle.

Both ways a live agent hands a stage back are covered: `loregarden_complete_stage`
over MCP, and an outside harness's `loregarden_finish_external_stage`. The
refusal is an `isError` result naming each unsettled criterion and the tool to
call; nothing is written, so the same call succeeds once they are settled. A
reject is never refused.
"""

from __future__ import annotations

import json

import pytest
from loregarden.mcp.protocol import handle_request
from loregarden.models.domain import (
    AgentRun,
    ExternalHarness,
    OrchestrationDriver,
    OrchestrationRun,
    OrchestrationRunStatus,
    RunStatus,
    StageReportStatus,
    StageStatus,
    Ticket,
    WorkflowTemplate,
    WorkItemType,
    Workspace,
    utcnow,
)
from loregarden.services.acceptance_criteria import serialize_criteria
from loregarden.services.criterion_ledger import settle_met
from loregarden.services.external_harness import EXTERNAL_HARNESS_COMMAND_PREFIX
from loregarden.services.orchestration import OrchestrationService
from loregarden.services.seed import seed_database
from loregarden.services.workflow_service import resolve_ticket_stages, workflow_instance_for
from loregarden.services.workflow_state import parse_stage_map, set_stage_status
from loregarden.testing.factories import COMPLETE_BLOCKING_FINDING, findings_fragment
from sqlmodel import Session, select
from tests.factories import make_workspace_ticket

CRITERIA = ["Width is read on every arm", "Errors name the arm"]

#: The live V3 spine: verify settles the criteria, the gate checks them.
V3_SHAPE = [
    {"key": "spec", "name": "Spec", "agent_id": "spec", "order": 1},
    {"key": "implement", "name": "Implement", "agent_id": "backend_implementer", "order": 2},
    {"key": "verify", "name": "Verify", "agent_id": "verifier", "order": 3},
    {"key": "review", "name": "Review", "agent_id": "architecture_reviewer", "order": 4},
    {"key": "gate", "name": "Gate", "agent_id": "gatekeeper", "order": 5, "stage_type": "gate"},
    {"key": "done", "name": "Done", "order": 6, "terminal": True},
]

PASS_REPORT = (
    "Verified.\n<<<LOREGARDEN_STAGE_REPORT>>>\n"
    '{"status": "pass", "confidence": 0.9}\n<<<END_STAGE_REPORT>>>\n'
)
REWORK_REPORT = (
    "A test fails.\n<<<LOREGARDEN_STAGE_REPORT>>>\n"
    '{"status": "needs_rework", "confidence": 0.9, "reroute_to_stage": "implement", '
    '"reroute_context": "Errors do not name the arm", '
    f'"unmet_criteria": ["Errors name the arm"]{findings_fragment(StageReportStatus.NEEDS_REWORK)}}}'
    "\n<<<END_STAGE_REPORT>>>\n"
)


@pytest.fixture(name="ticket")
def ticket_fixture(db_session: Session) -> Ticket:
    """A feature whose verify stage is running, its criteria not yet settled."""
    seed_database(db_session)
    workspace = db_session.exec(select(Workspace).where(Workspace.slug == "loregarden")).one()
    template = WorkflowTemplate(
        slug="settle-check-shape", name="settle check shape", stages_json=json.dumps(V3_SHAPE)
    )
    db_session.add(template)
    db_session.flush()
    workspace.workflow_template_id = template.id
    db_session.add(workspace)
    db_session.commit()
    milestone = make_workspace_ticket(db_session, "sc-milestone")
    milestone.work_item_type = WorkItemType.MILESTONE
    ticket = make_workspace_ticket(db_session, "sc-ticket")
    ticket.work_item_type = WorkItemType.FEATURE
    ticket.parent_ticket_id = milestone.id
    ticket.acceptance_criteria_json = serialize_criteria(CRITERIA)
    db_session.add_all([milestone, ticket])
    db_session.commit()
    OrchestrationService(db_session).ensure_workflow_instance(ticket, commit=True)
    instance = workflow_instance_for(db_session, ticket.id)
    _, stages = resolve_ticket_stages(db_session, ticket)
    for key in ("spec", "implement"):
        set_stage_status(ticket, instance, stages, key, StageStatus.DONE)
    set_stage_status(ticket, instance, stages, "verify", StageStatus.RUNNING)
    db_session.add_all([ticket, instance])
    db_session.commit()
    return ticket


def _orch_run(session: Session, ticket: Ticket, *, harness: ExternalHarness | None = None):
    run = OrchestrationRun(
        run_code=f"o-{ticket.external_id}",
        ticket_id=ticket.id,
        workspace_id=ticket.workspace_id,
        profile_slug="loregarden",
        driver=OrchestrationDriver.EXTERNAL_MCP,
        external_harness=harness,
        status=OrchestrationRunStatus.RUNNING,
        current_stage_key="verify",
        started_at=utcnow(),
    )
    session.add(run)
    session.commit()
    return run


def _settle_all(session: Session, ticket: Ticket) -> None:
    for criterion in CRITERIA:
        settle_met(
            session,
            ticket,
            criterion,
            tests=["server/tests/test_arms.py::test_reads_width"],
            evidence_artifact_id=None,
            commit_sha="",
            settled_by="run:v",
            reason="the test reads every arm",
        )


def _verify_status(session: Session, ticket: Ticket) -> StageStatus:
    session.refresh(ticket)
    _, stages = resolve_ticket_stages(session, ticket)
    return parse_stage_map(workflow_instance_for(session, ticket.id), stages)["verify"]


def _call(session: Session, name: str, arguments: dict) -> dict:
    response = handle_request(
        session,
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
        },
    )
    return response["result"]


def _complete_verify(session: Session, run: OrchestrationRun, outcome: str) -> dict:
    arguments = {"run_id": run.id, "stage_key": "verify", "outcome": outcome}
    if outcome == "reject":
        arguments["next_stage_key"] = "implement"
        arguments["findings"] = [COMPLETE_BLOCKING_FINDING]
    return _call(session, "loregarden_complete_stage", arguments)


def _external_run(session: Session, ticket: Ticket, orch: OrchestrationRun) -> AgentRun:
    run = AgentRun(
        run_code=f"r-{ticket.external_id}",
        ticket_id=ticket.id,
        workspace_id=ticket.workspace_id,
        orchestration_run_id=orch.id,
        agent_id="verifier",
        stage_key="verify",
        status=RunStatus.RUNNING,
        command=f"{EXTERNAL_HARNESS_COMMAND_PREFIX} claude_code",
        external_harness=ExternalHarness.CLAUDE_CODE,
        started_at=utcnow(),
    )
    session.add(run)
    session.commit()
    return run


def _finish(session: Session, run: AgentRun, transcript: str) -> dict:
    return _call(
        session,
        "loregarden_finish_external_stage",
        {"agent_run_id": run.id, "transcript": transcript},
    )


def test_a_verify_pass_with_an_unsettled_criterion_is_refused_and_names_it(db_session, ticket):
    run = _orch_run(db_session, ticket)

    result = _complete_verify(db_session, run, "pass")

    assert result["isError"] is True
    text = result["content"][0]["text"]
    for criterion in CRITERIA:
        assert f"- [unsettled] {criterion}" in text
    assert "loregarden_settle_criterion" in text
    assert "loregarden_complete_stage" in text
    # Refused before anything was written: verify is still the agent's to finish.
    assert _verify_status(db_session, ticket) is StageStatus.RUNNING


def test_once_settled_the_same_complete_stage_call_succeeds(db_session, ticket):
    run = _orch_run(db_session, ticket)
    assert _complete_verify(db_session, run, "pass")["isError"] is True

    _settle_all(db_session, ticket)
    result = _complete_verify(db_session, run, "pass")

    assert not result.get("isError"), result
    assert _verify_status(db_session, ticket) is StageStatus.DONE


def test_a_reject_from_verify_is_never_refused_for_unsettled_criteria(db_session, ticket):
    run = _orch_run(db_session, ticket)

    result = _complete_verify(db_session, run, "reject")

    assert not result.get("isError"), result
    assert _verify_status(db_session, ticket) is not StageStatus.RUNNING


def test_a_pass_from_a_stage_that_settles_nothing_is_not_checked(db_session, ticket):
    """Only the settling stage owes settlements: review passing with them open is fine."""
    instance = workflow_instance_for(db_session, ticket.id)
    _, stages = resolve_ticket_stages(db_session, ticket)
    set_stage_status(ticket, instance, stages, "verify", StageStatus.DONE)
    set_stage_status(ticket, instance, stages, "review", StageStatus.RUNNING)
    db_session.add_all([ticket, instance])
    db_session.commit()
    run = _orch_run(db_session, ticket)

    result = _call(
        db_session,
        "loregarden_complete_stage",
        {"run_id": run.id, "stage_key": "review", "outcome": "pass"},
    )

    assert not result.get("isError"), result


def test_an_external_harness_pass_with_an_unsettled_criterion_is_refused(db_session, ticket):
    orch = _orch_run(db_session, ticket, harness=ExternalHarness.CLAUDE_CODE)
    run = _external_run(db_session, ticket, orch)

    result = _finish(db_session, run, PASS_REPORT)

    assert result["isError"] is True
    text = result["content"][0]["text"]
    for criterion in CRITERIA:
        assert f"- [unsettled] {criterion}" in text
    assert "loregarden_finish_external_stage" in text
    db_session.refresh(run)
    assert run.status is RunStatus.RUNNING


def test_once_settled_the_same_external_finish_succeeds(db_session, ticket):
    orch = _orch_run(db_session, ticket, harness=ExternalHarness.CLAUDE_CODE)
    run = _external_run(db_session, ticket, orch)
    assert _finish(db_session, run, PASS_REPORT)["isError"] is True

    _settle_all(db_session, ticket)
    result = _finish(db_session, run, PASS_REPORT)

    assert not result.get("isError"), result
    db_session.refresh(run)
    assert run.status is RunStatus.SUCCEEDED
    assert _verify_status(db_session, ticket) is StageStatus.DONE


def test_an_external_rework_report_is_never_refused(db_session, ticket):
    orch = _orch_run(db_session, ticket, harness=ExternalHarness.CLAUDE_CODE)
    run = _external_run(db_session, ticket, orch)

    result = _finish(db_session, run, REWORK_REPORT)

    assert not result.get("isError"), result
    db_session.refresh(run)
    assert run.status is RunStatus.SUCCEEDED
