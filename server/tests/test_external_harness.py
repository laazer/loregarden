"""Running a ticket from a harness outside this control plane.

The operator copies a prompt into their own Claude Code / Codex session; that
session drives the ticket over MCP. Three things have to hold for the results to
be worth anything: the run is attributed to the harness, it is timed, and it
never touches the lane queue — it spawns nothing on this machine.
"""

import json
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from loregarden.mcp.tools import execute_tool
from loregarden.models.domain import (
    AgentRun,
    ExternalHarness,
    OrchestrationRun,
    OrchestrationRunStatus,
    QueuedRun,
    RunStatus,
    StageStatus,
    Ticket,
)
from loregarden.services.external_harness import (
    EXTERNAL_HARNESS_COMMAND_PREFIX,
    begin_external_stage,
    build_external_harness_prompt,
    finish_external_stage,
    start_external_orchestration,
)
from loregarden.services.orchestration import OrchestrationService
from loregarden.services.run_service import (
    fail_interrupted_orchestration_runs,
    fail_interrupted_runs,
)
from sqlmodel import Session, select

TICKET_SLUG = "03-wire-cli-agent-runner"

PASSING_REPORT = """
Did the work.

<<<LOREGARDEN_STAGE_REPORT>>>
{"status": "pass", "confidence": 0.9}
<<<END_STAGE_REPORT>>>
"""


def _ticket(session: Session) -> Ticket:
    return session.exec(select(Ticket).where(Ticket.legacy_external_id == TICKET_SLUG)).first()


def test_prompt_names_the_harness_and_the_calls_that_record_progress(db_session: Session):
    view = build_external_harness_prompt(
        db_session, _ticket(db_session), harness=ExternalHarness.CODEX
    )

    assert view.harness == ExternalHarness.CODEX
    prompt = view.prompt
    # The identity that makes the run comparable, and the tools that record it.
    assert 'external_harness="codex"' in prompt
    assert "loregarden_start_orchestration" in prompt
    assert "loregarden_begin_external_stage" in prompt
    assert "loregarden_finish_external_stage" in prompt
    assert "loregarden_complete_orchestration" in prompt
    assert view.ticket_id in prompt
    assert view.external_id in prompt


def test_copying_a_prompt_starts_nothing(client: TestClient, db_session: Session):
    ticket = _ticket(db_session)
    before = len(db_session.exec(select(OrchestrationRun)).all())

    response = client.post(
        f"/api/tickets/{ticket.id}/external_harness_prompt",
        json={"harness": "claude_code"},
    )

    assert response.status_code == 200
    assert response.json()["harness"] == "claude_code"
    # A prompt copied and never pasted must not leave a run open, or its start
    # time would be when the operator clicked a menu item.
    assert len(db_session.exec(select(OrchestrationRun)).all()) == before


def test_external_orchestration_never_reserves_a_lane(db_session: Session):
    ticket = _ticket(db_session)

    def _fail(*args, **kwargs):
        raise AssertionError("an external-harness run must not go through admission")

    with patch("loregarden.mcp.tools.start_orchestration_admitted", _fail):
        payload = json.loads(
            execute_tool(
                db_session,
                "loregarden_start_orchestration",
                {"ticket_id": ticket.id, "external_harness": "claude_code"},
            )
        )

    assert payload["external_harness"] == "claude_code"
    assert payload["status"] == OrchestrationRunStatus.RUNNING.value
    assert payload["started_at"]
    assert db_session.exec(select(QueuedRun).where(QueuedRun.ticket_id == ticket.id)).all() == []


def test_the_stage_pair_is_reachable_over_mcp(db_session: Session):
    """The harness only ever reaches these through the MCP transport."""
    ticket = _ticket(db_session)
    orch_run = start_external_orchestration(db_session, ticket, harness=ExternalHarness.CODEX)

    stage = json.loads(
        execute_tool(db_session, "loregarden_begin_external_stage", {"run_id": orch_run.id})
    )
    assert len(stage["runs"]) == 1
    assert stage["runs"][0]["agent_run_id"]
    assert stage["runs"][0]["prompt"]

    result = json.loads(
        execute_tool(
            db_session,
            "loregarden_finish_external_stage",
            {"agent_run_id": stage["runs"][0]["agent_run_id"], "transcript": PASSING_REPORT},
        )
    )
    assert result["status"] == RunStatus.SUCCEEDED.value
    assert result["finished_at"]


def test_stage_round_trip_is_attributed_and_timed(db_session: Session):
    ticket = _ticket(db_session)
    orch_run = start_external_orchestration(db_session, ticket, harness=ExternalHarness.CLAUDE_CODE)

    stage = begin_external_stage(db_session, orch_run)
    assert len(stage.runs) == 1
    assert stage.runs[0].agent_run_id
    assert stage.runs[0].prompt
    assert stage.stage_key

    run = db_session.get(AgentRun, stage.runs[0].agent_run_id)
    assert run.external_harness == ExternalHarness.CLAUDE_CODE
    assert run.command.startswith(EXTERNAL_HARNESS_COMMAND_PREFIX)
    assert run.orchestration_run_id == orch_run.id

    result = finish_external_stage(db_session, run, transcript=PASSING_REPORT)

    assert result.status == RunStatus.SUCCEEDED
    assert result.duration_seconds >= 0
    assert result.finished_at is not None
    # The stage report routed the workflow, exactly as it would for a supervised run.
    assert result.workflow_stage_status == StageStatus.DONE
    assert not result.blocking_issues


def test_a_stage_report_that_rejects_reroutes_the_same_way(db_session: Session):
    ticket = _ticket(db_session)
    orch_run = start_external_orchestration(db_session, ticket, harness=ExternalHarness.CODEX)
    stage = begin_external_stage(db_session, orch_run)
    run = db_session.get(AgentRun, stage.runs[0].agent_run_id)

    result = finish_external_stage(
        db_session,
        run,
        transcript=(
            "<<<LOREGARDEN_STAGE_REPORT>>>\n"
            '{"status": "needs_rework", "reroute_context": "tests miss the reported case"}\n'
            "<<<END_STAGE_REPORT>>>\n"
        ),
    )

    assert result.status == RunStatus.SUCCEEDED
    assert result.workflow_stage_status != StageStatus.DONE


def test_the_restart_reapers_leave_external_runs_alone(db_session: Session):
    """A harness in someone's terminal is not orphaned by this server restarting."""
    ticket = _ticket(db_session)
    orch_run = start_external_orchestration(db_session, ticket, harness=ExternalHarness.CLAUDE_CODE)
    stage = begin_external_stage(db_session, orch_run)

    fail_interrupted_runs(db_session)
    fail_interrupted_orchestration_runs(db_session)

    run = db_session.get(AgentRun, stage.runs[0].agent_run_id)
    db_session.refresh(run)
    db_session.refresh(orch_run)
    assert run.status == RunStatus.RUNNING
    assert orch_run.status == OrchestrationRunStatus.RUNNING


def test_finishing_a_run_no_harness_checked_out_is_refused(db_session: Session):
    """A supervised run must not settle through the external path."""
    run = OrchestrationService(db_session).start_run(_ticket(db_session))

    with pytest.raises(ValueError, match="external harness"):
        finish_external_stage(db_session, run, transcript=PASSING_REPORT)
