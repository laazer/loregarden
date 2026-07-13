"""Parallel-stage reroute preference: an agent-specified reroute_to_stage
(from the structured stage report) must win over the workflow template's
`reject` transition — previously the template's reject route was the only
signal ever consulted.
"""

from loregarden.core.workflow_loader import get_template_stages, sync_workflow_templates
from loregarden.models.domain import (
    AgentRun,
    OrchestrationRun,
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
from loregarden.services.workflow_state import initial_stages_json
from sqlmodel import Session, select


def _stage_report(
    status: str, confidence: float, reroute_to_stage: str = "", reroute_context: str = ""
) -> str:
    extra = ""
    if reroute_to_stage:
        extra += f', "reroute_to_stage": "{reroute_to_stage}"'
    if reroute_context:
        extra += f', "reroute_context": "{reroute_context}"'
    return (
        "Narrative output from the agent.\n"
        "<<<LOREGARDEN_STAGE_REPORT>>>\n"
        f'{{"status": "{status}", "confidence": {confidence}{extra}}}\n'
        "<<<END_STAGE_REPORT>>>\n"
    )


def _setup_script_review_ticket(db_session: Session):
    sync_workflow_templates(db_session)
    template = db_session.exec(
        select(WorkflowTemplate).where(WorkflowTemplate.slug == "blobert-tdd")
    ).first()
    assert template is not None
    ws = db_session.exec(select(Workspace).where(Workspace.slug == "loregarden")).first()
    assert ws is not None

    stages = get_template_stages(template)
    ticket = Ticket(
        external_id="parallel-reroute-test",
        workspace_id=ws.id,
        title="Parallel reroute test",
        description="Verify agent-specified reroute wins over template reject",
        state=TicketState.IN_PROGRESS,
        work_item_type=WorkItemType.TASK,
        workflow_stage_key="script_review",
        workflow_stage_status=StageStatus.RUNNING,
        next_agent="gdscript_reviewer",
    )
    db_session.add(ticket)
    db_session.commit()
    db_session.refresh(ticket)

    stage_map = {s.key: StageStatus.DONE for s in stages if s.key != "script_review"}
    stage_map["script_review"] = StageStatus.RUNNING
    instance = WorkflowInstance(
        ticket_id=ticket.id,
        template_id=template.id,
        current_stage_key="script_review",
        stages_json=initial_stages_json(stages),
    )
    db_session.add(instance)
    db_session.commit()

    orch_run = OrchestrationRun(
        run_code="orch_test_reroute",
        ticket_id=ticket.id,
        workspace_id=ws.id,
        current_stage_key="script_review",
    )
    db_session.add(orch_run)
    db_session.commit()

    script_review_def = next(s for s in stages if s.key == "script_review")
    return ticket, orch_run, script_review_def


def test_agent_specified_reroute_preferred_over_template_reject(db_session: Session, monkeypatch):
    from loregarden.agents.executors.cli import CliAgentExecutor

    ticket, orch_run, script_review_def = _setup_script_review_ticket(db_session)

    def fake_execute(self, run: AgentRun, worker_ticket: Ticket, **kwargs):
        if run.agent_id == "static_qa":
            run.status = RunStatus.SUCCEEDED  # clean exit, but self-reports failure
            run.stdout = _stage_report(
                "fail",
                0.9,
                reroute_to_stage="specification",
                reroute_context="Spec missing acid weak point section",
            )
        else:
            run.status = RunStatus.SUCCEEDED
            run.stdout = _stage_report("pass", 0.95)
        run.stderr = ""
        self.session.add(run)
        self.session.commit()
        return run

    monkeypatch.setattr(CliAgentExecutor, "execute", fake_execute)

    builtin = BuiltinOrchestrator(db_session)
    ok, message = builtin._execute_parallel_stage(
        ticket, orch_run, script_review_def, "script_review"
    )

    assert ok is True
    db_session.refresh(ticket)
    # The template's own `script_review -> reject -> implementation` transition
    # exists (test_blobert_template_includes_reject_transitions), but the
    # agent's explicit reroute_to_stage must win.
    assert ticket.workflow_stage_key == "specification"
    assert "acid weak point" in ticket.blocking_issues


def test_falls_back_to_template_reject_when_no_agent_reroute(db_session: Session, monkeypatch):
    from loregarden.agents.executors.cli import CliAgentExecutor

    ticket, orch_run, script_review_def = _setup_script_review_ticket(db_session)

    def fake_execute(self, run: AgentRun, worker_ticket: Ticket, **kwargs):
        run.status = RunStatus.FAILED  # process failure, no stage report at all
        run.stdout = ""
        run.stderr = "crashed"
        self.session.add(run)
        self.session.commit()
        return run

    monkeypatch.setattr(CliAgentExecutor, "execute", fake_execute)

    builtin = BuiltinOrchestrator(db_session)
    ok, message = builtin._execute_parallel_stage(
        ticket, orch_run, script_review_def, "script_review"
    )

    assert ok is True
    db_session.refresh(ticket)
    assert ticket.workflow_stage_key == "implementation"
