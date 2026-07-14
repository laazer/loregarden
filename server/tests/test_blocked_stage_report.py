"""Regression test: an agent self-reporting `status: "blocked"` on a clean process
exit must halt the ticket, not silently advance to the next stage.

Before the fix, `"blocked"` wasn't in `_VALID_STATUSES`, so `parse_stage_report`
discarded the whole report (returned None) and `_advance_stage_after_run` fell
back to its exit-code-only branch — marking the stage DONE and letting the
workflow proceed into the next stage (e.g. `script_review` running right after
an `implementation` stage that had actually reported itself blocked).
"""

from loregarden.core.workflow_loader import get_template_stages, sync_workflow_templates
from loregarden.models.domain import (
    AgentRun,
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
from loregarden.services.workflow_state import initial_stages_json, parse_stage_map
from sqlmodel import Session, select


def _blocked_report(context: str) -> str:
    return (
        "Narrative output from the agent.\n"
        "<<<LOREGARDEN_STAGE_REPORT>>>\n"
        f'{{"status": "blocked", "confidence": 0.9, "reroute_context": "{context}"}}\n'
        "<<<END_STAGE_REPORT>>>\n"
    )


def test_blocked_report_halts_ticket_instead_of_advancing(db_session: Session):
    sync_workflow_templates(db_session)
    template = db_session.exec(
        select(WorkflowTemplate).where(WorkflowTemplate.slug == "blobert-tdd")
    ).first()
    assert template is not None
    ws = db_session.exec(select(Workspace).where(Workspace.slug == "loregarden")).first()
    assert ws is not None

    stages = get_template_stages(template)
    ticket = Ticket(
        external_id="blocked-report-test",
        workspace_id=ws.id,
        title="Blocked report test",
        description="Verify a blocked self-report halts rather than advances",
        state=TicketState.IN_PROGRESS,
        work_item_type=WorkItemType.TASK,
        workflow_stage_key="implementation",
        workflow_stage_status=StageStatus.RUNNING,
        next_agent="core_simulation",
    )
    db_session.add(ticket)
    db_session.commit()
    db_session.refresh(ticket)

    stage_map = {s.key: StageStatus.DONE for s in stages if s.key != "implementation"}
    stage_map["implementation"] = StageStatus.RUNNING
    instance = WorkflowInstance(
        ticket_id=ticket.id,
        template_id=template.id,
        current_stage_key="implementation",
        stages_json=initial_stages_json(stages),
    )
    db_session.add(instance)
    db_session.commit()

    run = AgentRun(
        run_code="orch_test_blocked",
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
        status=RunStatus.SUCCEEDED,  # clean process exit
        stdout=_blocked_report("needs a human decision on asset licensing"),
        stderr="",
    )

    db_session.refresh(ticket)
    db_session.refresh(instance)
    resolved_stage_map = parse_stage_map(instance, stages)

    assert resolved_stage_map["implementation"] == StageStatus.BLOCKED
    assert ticket.workflow_stage_key == "implementation"
    assert ticket.state == TicketState.BLOCKED
    assert "asset licensing" in ticket.blocking_issues
