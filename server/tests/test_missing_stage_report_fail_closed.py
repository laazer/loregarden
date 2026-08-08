"""Fail-closed: SUCCEEDED without a stage report must not advance the stage.

Regression for the AC-gate path where a gatekeeper rejected in prose (and/or
had MCP complete_stage cancelled) but the CLI still exited 0 — the orchestrator
previously treated that as pass and promoted the ticket into playtest.
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


def _pass_report() -> str:
    return (
        "Narrative.\n"
        "<<<LOREGARDEN_STAGE_REPORT>>>\n"
        '{"status": "pass", "confidence": 0.9, "reroute_to_stage": null, "reroute_context": ""}\n'
        "<<<END_STAGE_REPORT>>>\n"
    )


def _setup_implementation_ticket(
    db_session: Session, external_id: str
) -> tuple[Ticket, Workspace, WorkflowTemplate]:
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
        title="Missing stage report fail-closed",
        description="Verify clean exit without report blocks",
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
    return ticket, ws, template


def test_succeeded_without_stage_report_blocks_instead_of_advancing(db_session: Session):
    ticket, ws, template = _setup_implementation_ticket(db_session, "missing-report-blocks")

    run = AgentRun(
        run_code="orch_missing_report",
        ticket_id=ticket.id,
        workspace_id=ws.id,
        agent_id="ac_gatekeeper",
        skill_name="",
        stage_key="implementation",
        status=RunStatus.QUEUED,
    )
    db_session.add(run)
    db_session.commit()

    orch = OrchestrationService(db_session)
    orch.complete_run(
        run,
        status=RunStatus.SUCCEEDED,
        stdout=(
            "AC Gate decision: **REJECT**.\nI attempted MCP complete_stage but it was cancelled.\n"
        ),
        stderr="",
    )

    db_session.refresh(ticket)
    instance = db_session.exec(
        select(WorkflowInstance).where(WorkflowInstance.ticket_id == ticket.id)
    ).first()
    assert instance is not None
    stages = get_template_stages(template)
    resolved = parse_stage_map(instance, stages)

    assert resolved["implementation"] == StageStatus.BLOCKED
    assert ticket.workflow_stage_key == "implementation"
    assert ticket.state == TicketState.BLOCKED
    assert "LOREGARDEN_STAGE_REPORT" in ticket.blocking_issues


def test_usage_limit_blocks_with_the_provider_reason_not_the_missing_report(
    db_session: Session,
):
    """A quota-killed run still blocks, but must not read as a missing report.

    The CLI exits 0 after printing the provider's limit sentence, so this landed
    in the branch above and told the operator to make the agent emit a stage
    report — advice that cannot work until the window resets.
    """
    ticket, ws, template = _setup_implementation_ticket(db_session, "usage-limit-blocks")

    run = AgentRun(
        run_code="orch_usage_limit",
        ticket_id=ticket.id,
        workspace_id=ws.id,
        agent_id="core_simulation",
        skill_name="",
        stage_key="implementation",
        status=RunStatus.QUEUED,
    )
    db_session.add(run)
    db_session.commit()

    orch = OrchestrationService(db_session)
    orch.complete_run(
        run,
        status=RunStatus.SUCCEEDED,
        stdout=(
            "You've hit your usage limit. Upgrade to Pro "
            "(https://chatgpt.com/explore/pro), visit "
            "https://chatgpt.com/codex/settings/usage to purchase more credits or "
            "try again at Aug 12th, 2026 10:07 AM."
        ),
        stderr="",
    )

    db_session.refresh(ticket)
    instance = db_session.exec(
        select(WorkflowInstance).where(WorkflowInstance.ticket_id == ticket.id)
    ).first()
    assert instance is not None
    resolved = parse_stage_map(instance, get_template_stages(template))

    assert resolved["implementation"] == StageStatus.BLOCKED
    assert ticket.state == TicketState.BLOCKED
    assert "Usage limit reached on Codex / ChatGPT" in ticket.blocking_issues
    assert "Aug 12, 2026 10:07 AM" in ticket.blocking_issues
    assert "LOREGARDEN_STAGE_REPORT" not in ticket.blocking_issues


def test_succeeded_with_pass_report_still_advances(db_session: Session):
    ticket, ws, template = _setup_implementation_ticket(db_session, "pass-report-advances")

    run = AgentRun(
        run_code="orch_pass_report",
        ticket_id=ticket.id,
        workspace_id=ws.id,
        agent_id="core_simulation",
        skill_name="",
        stage_key="implementation",
        status=RunStatus.QUEUED,
    )
    db_session.add(run)
    db_session.commit()

    orch = OrchestrationService(db_session)
    orch.complete_run(
        run,
        status=RunStatus.SUCCEEDED,
        stdout=_pass_report(),
        stderr="",
    )

    db_session.refresh(ticket)
    instance = db_session.exec(
        select(WorkflowInstance).where(WorkflowInstance.ticket_id == ticket.id)
    ).first()
    assert instance is not None
    stages = get_template_stages(template)
    resolved = parse_stage_map(instance, stages)

    assert resolved["implementation"] == StageStatus.DONE
    assert ticket.state == TicketState.IN_PROGRESS
    assert ticket.blocking_issues == ""
