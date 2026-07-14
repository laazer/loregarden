"""A workflow transition gate (lint/static-analysis, todo/handoff, etc.)
failing after a stage's agent reports success must reroute the ticket back to
that same stage for another automatic pass, not hard-block it for a human —
distinct from an agent-reported fail/needs_rework, which is handled by
_advance_stage_after_run before this gate check ever runs.
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
from loregarden.services.builtin_orchestrator import BuiltinOrchestrator
from loregarden.services.orchestration_profile import GatesConfig, OrchestrationProfile
from loregarden.services.workflow_state import stages_up_to_done_json
from sqlmodel import Session, select


def _stage_report(status: str, confidence: float) -> str:
    return (
        "Narrative output from the agent.\n"
        "<<<LOREGARDEN_STAGE_REPORT>>>\n"
        f'{{"status": "{status}", "confidence": {confidence}}}\n'
        "<<<END_STAGE_REPORT>>>\n"
    )


def _setup_ticket_at_test_break(
    db_session: Session, tmp_path
) -> tuple[Ticket, OrchestrationProfile]:
    sync_workflow_templates(db_session)
    template = db_session.exec(
        select(WorkflowTemplate).where(WorkflowTemplate.slug == "blobert-tdd")
    ).first()
    assert template is not None

    ws = Workspace(slug="gate-reroute-test", name="Gate Reroute Test", repo_path=str(tmp_path))
    db_session.add(ws)
    db_session.commit()
    db_session.refresh(ws)

    stages = get_template_stages(template)
    ticket = Ticket(
        external_id="gate-reroute-test",
        workspace_id=ws.id,
        title="Gate reroute test",
        description="Verify a transition gate failure reroutes rather than hard-blocks",
        state=TicketState.IN_PROGRESS,
        work_item_type=WorkItemType.TASK,
        workflow_stage_key="test_break",
        workflow_stage_status=StageStatus.PENDING,
        next_agent="test_breaker",
    )
    db_session.add(ticket)
    db_session.commit()
    db_session.refresh(ticket)

    instance = WorkflowInstance(
        ticket_id=ticket.id,
        template_id=template.id,
        current_stage_key="test_break",
        stages_json=stages_up_to_done_json(stages, "test_design"),
    )
    db_session.add(instance)
    db_session.commit()

    profile = OrchestrationProfile(
        slug="gate-reroute-test",
        gates=GatesConfig(enabled=True, commands=["false"]),
    )
    return ticket, profile


def test_gate_failure_reroutes_to_same_stage_instead_of_blocking(
    db_session: Session, monkeypatch, tmp_path
):
    from loregarden.agents.executors.cli import CliAgentExecutor

    ticket, profile = _setup_ticket_at_test_break(db_session, tmp_path)

    def fake_execute(self, run: AgentRun, worker_ticket: Ticket, **kwargs):
        return self.orchestration.complete_run(
            run,
            status=RunStatus.SUCCEEDED,
            stdout=_stage_report("pass", 0.95),
            stderr="",
        )

    monkeypatch.setattr(CliAgentExecutor, "execute", fake_execute)

    builtin = BuiltinOrchestrator(db_session)
    builtin.execute(ticket, profile, max_stages=1)

    db_session.refresh(ticket)
    assert ticket.state != TicketState.BLOCKED
    assert ticket.workflow_stage_key == "test_break"
    assert ticket.workflow_stage_status == StageStatus.PENDING
    assert ticket.blocking_issues


def test_gate_passing_advances_normally(db_session: Session, monkeypatch, tmp_path):
    from loregarden.agents.executors.cli import CliAgentExecutor
    from loregarden.services.workflow_state import parse_stage_map

    ticket, profile = _setup_ticket_at_test_break(db_session, tmp_path)
    profile.gates.commands = ["true"]

    def fake_execute(self, run: AgentRun, worker_ticket: Ticket, **kwargs):
        return self.orchestration.complete_run(
            run,
            status=RunStatus.SUCCEEDED,
            stdout=_stage_report("pass", 0.95),
            stderr="",
        )

    monkeypatch.setattr(CliAgentExecutor, "execute", fake_execute)

    builtin = BuiltinOrchestrator(db_session)
    builtin.execute(ticket, profile, max_stages=1)

    db_session.refresh(ticket)
    instance, stages = builtin.orch._resolve_stages(ticket)
    stage_map = parse_stage_map(instance, stages)

    assert ticket.state != TicketState.BLOCKED
    assert not ticket.blocking_issues
    # A passing gate must not reroute test_break back to itself — it should be
    # marked DONE, same as if gates were disabled entirely.
    assert stage_map["test_break"] == StageStatus.DONE
