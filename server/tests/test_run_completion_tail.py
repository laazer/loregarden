"""What finishing a run records on the run itself, from the report it printed."""

import json

import pytest
from loregarden.models.domain import (
    AgentRun,
    DomainEvent,
    EventType,
    RunStatus,
    StageVerdictChannel,
    Workspace,
)
from loregarden.services.orchestration import OrchestrationService
from sqlmodel import Session, select
from tests.worktree_helpers import make_run, make_ticket


def _report(**fields) -> str:
    payload = {"status": "pass", "confidence": 0.9, **fields}
    return f"done\n<<<LOREGARDEN_STAGE_REPORT>>>\n{json.dumps(payload)}\n<<<END_STAGE_REPORT>>>\n"


@pytest.fixture
def run(db_session: Session) -> AgentRun:
    workspace = db_session.exec(select(Workspace).where(Workspace.slug == "loregarden")).one()
    ticket = make_ticket(db_session, workspace, external_id="LG-TAIL-1")
    run = make_run(db_session, workspace, ticket, "r_tail_1")
    run.stage_key = "implement"
    db_session.add(run)
    db_session.commit()
    return run


def _finish(session: Session, run: AgentRun, stdout: str) -> AgentRun:
    OrchestrationService(session).complete_run(
        run, status=RunStatus.SUCCEEDED, stdout=stdout, advance_workflow=False
    )
    session.refresh(run)
    return run


def test_a_printed_report_is_recorded_as_the_sentinel_channel_with_its_notes(db_session, run):
    finished = _finish(db_session, run, _report(notes_for_next_stage="check the migration"))

    assert finished.verdict_channel is StageVerdictChannel.SENTINEL
    assert finished.next_stage_notes == "check the migration"


def test_a_run_that_used_the_tool_keeps_the_tool_channel(db_session, run):
    """An agent that called `complete_stage` and also printed the sentinel used the
    typed channel; counting the fallback would overstate the sentinel's share."""
    run.verdict_channel = StageVerdictChannel.TOOL
    db_session.add(run)
    db_session.commit()

    assert _finish(db_session, run, _report()).verdict_channel is StageVerdictChannel.TOOL


def test_a_run_that_printed_no_report_records_no_channel_and_no_notes(db_session, run):
    finished = _finish(db_session, run, "done, no report\n")

    assert finished.verdict_channel is StageVerdictChannel.UNKNOWN
    assert finished.next_stage_notes == ""


def test_finishing_a_run_announces_it_with_its_status(db_session, run):
    _finish(db_session, run, "done\n")

    events = db_session.exec(
        select(DomainEvent).where(
            DomainEvent.run_id == run.id, DomainEvent.type == EventType.AGENT_RUN_COMPLETED
        )
    ).all()
    assert [json.loads(e.payload_json)["status"] for e in events] == ["succeeded"]
