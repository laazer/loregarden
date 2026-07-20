"""Resuming a parallel stage after an interruption (e.g. a server restart mid-run)
must not redo members that had already succeeded before the crash — only the
member(s) that never finished should be relaunched, still running concurrently
alongside each other.
"""

from datetime import datetime, timedelta, timezone

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
from loregarden.services.run_service import INTERRUPTED_RUN_MESSAGE, fail_interrupted_runs
from loregarden.services.workflow_state import initial_stages_json, parse_stage_map
from sqlmodel import Session, select


def _passing_report(agent_id: str) -> str:
    return (
        f"{agent_id} narrative output.\n"
        "<<<LOREGARDEN_STAGE_REPORT>>>\n"
        '{"status": "pass", "confidence": 0.95}\n'
        "<<<END_STAGE_REPORT>>>\n"
    )


def _setup_interrupted_script_review(db_session: Session) -> tuple[Ticket, OrchestrationRun, list]:
    """A script_review ticket where gdscript_reviewer and static_qa already
    succeeded before a server restart, and architecture_reviewer was orphaned
    mid-run (then marked FAILED/BLOCKED by fail_interrupted_runs)."""
    sync_workflow_templates(db_session)
    template = db_session.exec(
        select(WorkflowTemplate).where(WorkflowTemplate.slug == "blobert-tdd")
    ).first()
    assert template is not None
    ws = db_session.exec(select(Workspace).where(Workspace.slug == "loregarden")).first()
    assert ws is not None

    stages = get_template_stages(template)
    ticket = Ticket(
        external_id="parallel-resume-test",
        workspace_id=ws.id,
        title="Parallel resume test",
        description="Verify already-succeeded reviewers are not rerun on resume",
        state=TicketState.IN_PROGRESS,
        work_item_type=WorkItemType.TASK,
        workflow_stage_key="script_review",
        workflow_stage_status=StageStatus.RUNNING,
    )
    db_session.add(ticket)
    db_session.commit()
    db_session.refresh(ticket)

    instance = WorkflowInstance(
        ticket_id=ticket.id,
        template_id=template.id,
        current_stage_key="script_review",
        stages_json=initial_stages_json(stages),
    )
    db_session.add(instance)
    db_session.commit()

    orch_run = OrchestrationRun(
        run_code="orch_test_resume",
        ticket_id=ticket.id,
        workspace_id=ws.id,
        current_stage_key="script_review",
    )
    db_session.add(orch_run)
    db_session.commit()

    # gdscript_reviewer and static_qa already finished successfully before the crash.
    now = datetime.now(timezone.utc)
    review_stage = next(s for s in stages if s.key == "script_review")
    lane_skill = {spec.agent_id: spec.skill_name for spec in review_stage.parallel_agents}
    for agent_id in ("gdscript_reviewer", "static_qa"):
        finished = AgentRun(
            run_code=f"prior_{agent_id}",
            ticket_id=ticket.id,
            workspace_id=ws.id,
            agent_id=agent_id,
            # Each lane's own skill, as _start_parallel_stage_runs records it.
            # Hardcoding one skill for every lane made static_qa's prior run
            # unmatchable, so resume could not tell it had already finished.
            skill_name=lane_skill[agent_id],
            stage_key="script_review",
            status=RunStatus.SUCCEEDED,
            stdout=_passing_report(agent_id),
            created_at=now - timedelta(minutes=5),
            finished_at=now - timedelta(minutes=4),
        )
        db_session.add(finished)
    db_session.commit()

    # architecture_reviewer was mid-run when the server died.
    orphan = AgentRun(
        run_code="orphan_architecture_reviewer",
        ticket_id=ticket.id,
        workspace_id=ws.id,
        agent_id="architecture_reviewer",
        skill_name=lane_skill["architecture_reviewer"],
        stage_key="script_review",
        status=RunStatus.RUNNING,
        created_at=now - timedelta(minutes=3),
    )
    db_session.add(orphan)
    db_session.commit()

    fail_interrupted_runs(db_session, ticket_id=ticket.id, stage_key="script_review")
    db_session.refresh(ticket)
    assert ticket.workflow_stage_status == StageStatus.BLOCKED
    assert ticket.blocking_issues == INTERRUPTED_RUN_MESSAGE

    return ticket, orch_run, stages


def test_resume_only_reruns_incomplete_parallel_members(db_session: Session, monkeypatch):
    from loregarden.agents.executors.cli import CliAgentExecutor

    ticket, orch_run, stages = _setup_interrupted_script_review(db_session)

    executed_agents: list[str] = []

    def fake_execute(self, run: AgentRun, worker_ticket: Ticket, **kwargs):
        executed_agents.append(run.agent_id)
        run.status = RunStatus.SUCCEEDED
        run.stdout = _passing_report(run.agent_id)
        run.stderr = ""
        self.session.add(run)
        self.session.commit()
        return run

    monkeypatch.setattr(CliAgentExecutor, "execute", fake_execute)

    builtin = BuiltinOrchestrator(db_session)
    instance, stages = builtin.orch._resolve_stages(ticket)
    recovered_stage_key = builtin._recover_interrupted_stage(ticket, instance, stages)
    assert recovered_stage_key == "script_review"

    db_session.refresh(ticket)
    script_review_def = next(s for s in stages if s.key == "script_review")
    ok, message = builtin._execute_parallel_stage(
        ticket, orch_run, script_review_def, "script_review", resuming=True
    )

    assert ok is True
    # Only the interrupted reviewer should have actually run again.
    assert executed_agents == ["architecture_reviewer"]

    db_session.refresh(ticket)
    db_session.refresh(instance)
    assert parse_stage_map(instance, stages)["script_review"] == StageStatus.DONE
