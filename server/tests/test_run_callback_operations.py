"""The REST callbacks run the same operations as the MCP tools.

Each REST callback under `/api/orchestration/runs/{run_id}/` was a second copy
of an MCP tool, and each copy had lost something: `start_stage` reserved no
slot and could not override a prerequisite, `complete_stage` dropped findings,
`block` dropped the prepared action, and `attach_artifact` recorded no source
run.
These tests pin what the REST path gained by running the operation.
"""

from __future__ import annotations

import json
from unittest import mock

import pytest
from loregarden.mcp.tool_ids import McpTool
from loregarden.mcp.tools import execute_tool
from loregarden.models.domain import Artifact, OrchestrationRun, OrchestratorDecision, Ticket
from loregarden.services.orchestration import OrchestrationService
from loregarden.services.orchestration_callbacks import OrchestrationCallbackService
from loregarden.services.queue_admission import QueueAdmissionService, Reservation
from loregarden.services.ticket_dependencies import TicketDependencyService
from sqlmodel import Session
from tests.factories import make_agent_run, make_orchestration_run, make_workspace_ticket
from tests.history_helpers import decision_kinds, decision_payloads


@pytest.fixture
def run(db_session: Session) -> OrchestrationRun:
    ticket = make_workspace_ticket(db_session, "lg-callback-ops")
    OrchestrationService(db_session).ensure_workflow_instance(ticket, commit=True)
    return make_orchestration_run(db_session, workspace_id=ticket.workspace_id, ticket_id=ticket.id)


def _post(client, run_id: str, callback: str, body: dict):
    return client.post(f"/api/orchestration/runs/{run_id}/{callback}", json=body)


def test_a_callback_naming_no_run_is_a_404(client):
    assert _post(client, "no-such-run", "skip_stage", {"stage_key": "plan"}).status_code == 404


def test_rest_start_stage_is_held_on_an_unfinished_prerequisite(client, db_session, run):
    prerequisite = make_workspace_ticket(db_session, "lg-callback-prereq")
    TicketDependencyService(db_session).add_dependency(run.ticket_id, prerequisite.id)

    response = _post(client, run.id, "start_stage", {"stage_key": "plan"})

    assert response.status_code == 409
    assert prerequisite.external_id in response.json()["detail"]


def test_rest_start_stage_waits_for_a_lane(client, run):
    full = Reservation(admitted=False, message="all lanes busy")
    with mock.patch.object(QueueAdmissionService, "reserve_stage", return_value=full):
        response = _post(client, run.id, "start_stage", {"stage_key": "plan"})

    assert response.status_code == 409
    assert response.json()["detail"]["status"] == "queued"
    assert response.json()["detail"]["stage_key"] == "plan"


def test_rest_attach_artifact_records_the_run_it_came_from(client, db_session, run):
    ticket = db_session.get(Ticket, run.ticket_id)
    stage_run = make_agent_run(
        db_session,
        workspace_id=ticket.workspace_id,
        ticket_id=ticket.id,
        run_code="run_cb0001",
        orchestration_run_id=run.id,
    )
    response = _post(
        client,
        stage_run.run_code,
        "attach_artifact",
        {"kind": "log", "title": "notes", "content": {"summary": "s"}},
    )

    assert response.status_code == 200
    artifact = db_session.get(Artifact, response.json()["artifact_id"])
    assert artifact.run_id == stage_run.id
    assert json.loads(artifact.content_json) == {"summary": "s"}


def test_rest_block_assesses_the_handover_as_mcp_does(client, db_session, run):
    body = {"stage_key": "plan", "message": "Please run the migration", "tier": "sideways"}

    rest = _post(client, run.id, "block", body)
    over_mcp_error = None
    try:
        execute_tool(db_session, McpTool.BLOCK_TICKET.value, {**body, "run_id": run.id})
    except ValueError as exc:
        over_mcp_error = str(exc)

    # An unknown tier is refused rather than filed at a rung the agent did not
    # claim, on both transports and in the same words.
    assert rest.status_code == 400
    assert over_mcp_error is not None
    assert rest.json()["detail"] == over_mcp_error


def test_rest_and_mcp_complete_stage_reply_with_one_shape(client, db_session, run):
    rest = _post(client, run.id, "complete_stage", {"stage_key": "plan", "advance": False})
    over_mcp = json.loads(
        execute_tool(
            db_session,
            McpTool.COMPLETE_STAGE.value,
            {"run_id": run.id, "stage_key": "plan"},
        )
    )

    assert rest.status_code == 200, rest.text
    assert set(rest.json()) == set(over_mcp)


def test_rest_complete_stage_keeps_its_findings(client, run):
    finding = {"summary": "the empty state says nothing", "blocking": False}
    with mock.patch.object(
        OrchestrationCallbackService, "complete_stage", return_value=None
    ) as complete:
        _post(client, run.id, "complete_stage", {"stage_key": "plan", "findings": [finding]})

    (passed,) = complete.call_args.kwargs["findings"]
    assert passed.summary == finding["summary"]


def test_rest_start_stage_can_start_past_a_prerequisite_on_the_record(client, db_session, run):
    prerequisite = make_workspace_ticket(db_session, "lg-callback-override")
    TicketDependencyService(db_session).add_dependency(run.ticket_id, prerequisite.id)

    response = _post(
        client, run.id, "start_stage", {"stage_key": "plan", "ignore_prerequisites": True}
    )

    assert response.status_code == 200, response.text
    assert OrchestratorDecision.PREREQUISITES_OVERRIDDEN.value in decision_kinds(
        db_session, run.ticket_id
    )
    assert "rest" in decision_payloads(db_session, run.ticket_id)[-1]["reason"]
