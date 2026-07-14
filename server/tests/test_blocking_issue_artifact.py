"""Regression test: raw agent/gate output that lands in ticket.blocking_issues
must be capped, with the full text filed as an error artifact for the Errors
tab — the workflow pane renders blocking_issues verbatim, so a long dump there
(agent reroute_context, stderr, or a directly-blocked message) is unreadable.

Mirrors the treatment already applied to transition-gate failures in
server/tests/test_transition_gate_reroute.py, but for the other code paths
that write ticket.blocking_issues: an agent's own "blocked" self-report
(orchestration.py) and OrchestrationCallbackService.block_ticket.
"""

import json

from loregarden.core.workflow_loader import get_template_stages, sync_workflow_templates
from loregarden.models.domain import (
    AgentRun,
    Artifact,
    OrchestrationRun,
    OrchestrationRunStatus,
    RunStatus,
    StageStatus,
    Ticket,
    TicketState,
    WorkflowInstance,
    WorkflowTemplate,
    WorkItemType,
    Workspace,
)
from loregarden.services.orchestration import OrchestrationService
from loregarden.services.orchestration_callbacks import OrchestrationCallbackService
from loregarden.services.workflow_state import initial_stages_json
from sqlmodel import Session, select

_RAW_DUMP = "lefthook pre-commit FAIL\n" + ("boom " * 100)


def _blocked_report(context: str) -> str:
    return (
        "Narrative output from the agent.\n"
        "<<<LOREGARDEN_STAGE_REPORT>>>\n"
        f'{{"status": "blocked", "confidence": 0.9, "reroute_context": {json.dumps(context)}}}\n'
        "<<<END_STAGE_REPORT>>>\n"
    )


def _setup_ticket(db_session: Session, *, external_id: str) -> tuple[Ticket, list, Workspace]:
    sync_workflow_templates(db_session)
    template = db_session.exec(
        select(WorkflowTemplate).where(WorkflowTemplate.slug == "blobert-tdd")
    ).first()
    assert template is not None
    ws = db_session.exec(select(Workspace).where(Workspace.slug == "loregarden")).first()
    assert ws is not None

    stages = get_template_stages(template)
    ticket = Ticket(
        external_id=external_id,
        workspace_id=ws.id,
        title="Blocking issue truncation test",
        description="Verify long raw output is filed as an error artifact",
        state=TicketState.IN_PROGRESS,
        work_item_type=WorkItemType.TASK,
        workflow_stage_key="implementation",
        workflow_stage_status=StageStatus.RUNNING,
        next_agent="core_simulation",
    )
    db_session.add(ticket)
    db_session.commit()
    db_session.refresh(ticket)

    instance = WorkflowInstance(
        ticket_id=ticket.id,
        template_id=template.id,
        current_stage_key="implementation",
        stages_json=initial_stages_json(stages),
    )
    db_session.add(instance)
    db_session.commit()
    return ticket, stages, ws


def test_blocked_self_report_with_long_context_files_error_artifact(db_session: Session):
    ticket, _stages, ws = _setup_ticket(db_session, external_id="blocked-long-context-test")

    run = AgentRun(
        run_code="orch_test_blocked_long",
        ticket_id=ticket.id,
        workspace_id=ws.id,
        agent_id="core_simulation",
        skill_name="apply_patch",
        stage_key="implementation",
        status=RunStatus.QUEUED,
    )
    db_session.add(run)
    db_session.commit()

    orch = OrchestrationService(db_session)
    orch.complete_run(
        run,
        status=RunStatus.SUCCEEDED,
        stdout=_blocked_report(_RAW_DUMP),
        stderr="",
    )

    db_session.refresh(ticket)
    assert ticket.state == TicketState.BLOCKED
    assert len(ticket.blocking_issues) < 200
    assert "Errors tab" in ticket.blocking_issues
    assert _RAW_DUMP not in ticket.blocking_issues

    error_artifacts = db_session.exec(
        select(Artifact).where(Artifact.ticket_id == ticket.id, Artifact.kind == "error")
    ).all()
    assert len(error_artifacts) == 1
    content = json.loads(error_artifacts[0].content_json)
    assert content["message"] == _RAW_DUMP
    assert content["stage_key"] == "implementation"


def test_block_ticket_with_long_message_files_error_artifact(db_session: Session):
    ticket, _stages, ws = _setup_ticket(db_session, external_id="block-ticket-long-message-test")

    orch_run = OrchestrationRun(
        run_code="orch_test_block_ticket",
        ticket_id=ticket.id,
        workspace_id=ws.id,
        current_stage_key="implementation",
        status=OrchestrationRunStatus.RUNNING,
    )
    db_session.add(orch_run)
    db_session.commit()

    callbacks = OrchestrationCallbackService(db_session)
    callbacks.block_ticket(
        orch_run,
        ticket,
        stage_key="implementation",
        message=_RAW_DUMP,
    )

    db_session.refresh(ticket)
    assert ticket.state == TicketState.BLOCKED
    assert len(ticket.blocking_issues) < 200
    assert "Errors tab" in ticket.blocking_issues
    assert _RAW_DUMP not in ticket.blocking_issues

    error_artifacts = db_session.exec(
        select(Artifact).where(Artifact.ticket_id == ticket.id, Artifact.kind == "error")
    ).all()
    assert len(error_artifacts) == 1
    content = json.loads(error_artifacts[0].content_json)
    assert content["message"] == _RAW_DUMP
    assert content["stage_key"] == "implementation"
