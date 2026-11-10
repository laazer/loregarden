"""A run records the spec it started from, and one whose spec changed underneath it is flagged.

lg-milestone-that-2489: a ticket's description, criteria and documents can move
while a stage runs (scope rounds, splits, delegations, operator edits), and the
run that started from the old text reported against it with nothing saying so.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest import mock
from uuid import uuid4

import pytest
from loregarden.agents.executors import stage_turn
from loregarden.agents.executors.cli import CliAgentExecutor
from loregarden.mcp.tools import execute_tool
from loregarden.models.domain import (
    AgentRun,
    DomainEvent,
    EventType,
    OrchestrationRun,
    OrchestrationRunStatus,
    OrchestratorDecision,
    RunStatus,
    StageStatus,
    Ticket,
    TicketState,
    WorkflowInstance,
    WorkflowStageDef,
    WorkflowTemplate,
    Workspace,
)
from loregarden.models.domain.enums import CliAdapter
from loregarden.models.domain.schemas import UpdateTicketRequest
from loregarden.services.criterion_ledger import unsettled
from loregarden.services.orchestration import OrchestrationService
from loregarden.services.rework_feedback import latest_rework_context
from loregarden.services.spec_snapshot import (
    SpecSnapshot,
    recorded_spec,
    spec_drift,
    spec_snapshot,
    stamp_run_spec,
)
from loregarden.services.ticket_documents import write_document
from loregarden.services.workflow_state import initial_stages_json, parse_stage_map
from sqlmodel import Session, select
from tests.factories import make_workspace_ticket
from tests.test_memory_briefing_seam import _fixture as briefing_fixture
from tests.test_rework_resume import _plan, _reject, _stream
from tests.test_rework_resume import _run as rework_run
from tests.worktree_helpers import make_repo

OLD = "The parser rejects an empty header."
NEW = "The parser rejects an empty header with a 400 naming the header."
KEPT = "A test covers it."


def _report(status: str = "pass") -> str:
    payload = json.dumps({"status": status, "confidence": 0.9})
    return f"\n<<<LOREGARDEN_STAGE_REPORT>>>\n{payload}\n<<<END_STAGE_REPORT>>>\n"


def _stage(key: str, order: int, agent_id: str = "", **extra) -> WorkflowStageDef:
    return WorkflowStageDef(
        key=key, name=key, agent_id=agent_id, order=order, exit_actions_enabled=False, **extra
    )


@pytest.fixture
def implementing(db_session: Session, tmp_path):
    """A ticket at `implement` with two criteria, and a run dispatched on them."""
    stages = [_stage("implement", 1, "backend_implementer"), _stage("done", 2, terminal=True)]
    template = WorkflowTemplate(
        slug=f"ss-{uuid4().hex[:8]}",
        name="Spec snapshot",
        stages_json=json.dumps([s.model_dump(mode="json") for s in stages]),
        transitions_json=json.dumps([{"from": "implement", "to": "done", "when": "pass"}]),
    )
    db_session.add(template)
    db_session.commit()
    workspace = Workspace(
        slug=f"ss-{uuid4().hex[:8]}",
        name="Spec snapshot",
        repo_path=str(tmp_path),
        workflow_template_id=template.id,
    )
    db_session.add(workspace)
    db_session.commit()
    ticket = Ticket(
        external_id=f"ss-{uuid4().hex[:8]}",
        workspace_id=workspace.id,
        title="Spec snapshot",
        description="Reject empty headers.",
        acceptance_criteria_json=json.dumps([OLD, KEPT]),
        state=TicketState.IN_PROGRESS,
        workflow_stage_key="implement",
        workflow_stage_status=StageStatus.RUNNING,
    )
    db_session.add(ticket)
    db_session.commit()
    db_session.add(
        WorkflowInstance(
            ticket_id=ticket.id,
            template_id=template.id,
            current_stage_key="implement",
            stages_json=initial_stages_json(stages),
        )
    )
    orch = OrchestrationRun(
        run_code=f"orch_{uuid4().hex[:8]}",
        ticket_id=ticket.id,
        workspace_id=workspace.id,
        current_stage_key="implement",
        status=OrchestrationRunStatus.RUNNING,
    )
    db_session.add(orch)
    db_session.commit()
    run = AgentRun(
        run_code=f"r_{uuid4().hex[:8]}",
        ticket_id=ticket.id,
        workspace_id=workspace.id,
        agent_id="backend_implementer",
        stage_key="implement",
        status=RunStatus.RUNNING,
        orchestration_run_id=orch.id,
    )
    db_session.add(run)
    db_session.commit()
    db_session.refresh(ticket)
    stamp_run_spec(db_session, run, ticket)
    return ticket, run


def _edit(session: Session, ticket: Ticket, **fields) -> None:
    """An operator's edit, from outside the run."""
    OrchestrationService(session).update_ticket_manual(ticket, UpdateTicketRequest(**fields))
    session.refresh(ticket)


def _finish(session: Session, ticket: Ticket, run: AgentRun, stdout: str = _report()) -> None:
    OrchestrationService(session).complete_run(run, status=RunStatus.SUCCEEDED, stdout=stdout)
    session.refresh(ticket)


def _flags(session: Session, ticket: Ticket) -> list[dict]:
    events = session.exec(
        select(DomainEvent).where(
            DomainEvent.ticket_id == ticket.id,
            DomainEvent.type == EventType.ORCHESTRATOR_DECISION,
        )
    ).all()
    payloads = [{**json.loads(event.payload_json), "run_id": event.run_id} for event in events]
    return [p for p in payloads if p["decision"] == OrchestratorDecision.SPEC_CHANGED_UNDER_RUN]


def _implement_status(session: Session, ticket: Ticket) -> StageStatus:
    instance, stages = OrchestrationService(session)._resolve_stages(ticket)
    return parse_stage_map(instance, stages)["implement"]


# ---- recorded at dispatch ------------------------------------------------------


def test_the_dispatched_spec_is_recorded_with_its_hash(db_session, implementing):
    ticket, run = implementing

    snapshot = recorded_spec(run)

    assert snapshot is not None
    assert snapshot.criteria == (OLD, KEPT)
    assert run.spec_hash == snapshot.digest() == spec_snapshot(db_session, ticket).digest()


def test_the_supervised_dispatch_records_the_spec(db_session):
    """Through `CliAgentExecutor.execute`, the path every supervised stage takes."""
    ticket = make_workspace_ticket(db_session, "spec-dispatch")
    run = rework_run(
        db_session, ticket, repo_root=Path("/nowhere"), code="run_1", status=RunStatus.RUNNING
    )

    with mock.patch.object(
        stage_turn, "run_print_mode", return_value=(_stream(), "", RunStatus.SUCCEEDED)
    ):
        completed = CliAgentExecutor(db_session).execute(run, ticket)

    assert completed.spec_hash == spec_snapshot(db_session, ticket).digest()


def test_a_rendered_stage_prompt_records_the_spec(isolated_db, tmp_path):
    """The handoff and external-harness paths build their prompt here."""
    execution_root = make_repo(tmp_path, "tree")
    with Session(isolated_db, expire_on_commit=False) as session:
        ticket, workspace, run, _ = briefing_fixture(session, tmp_path, seed_checkpoint=False)
        workspace.repo_path = str(execution_root)
        session.add(workspace)
        session.commit()
        with mock.patch(
            "loregarden.agents.executors.cli.resolve_execution_root",
            return_value=execution_root,
        ):
            CliAgentExecutor(session).render_stage_prompt(run, ticket)
        session.refresh(run)

        assert run.spec_hash == spec_snapshot(session, ticket).digest()


# ---- flagged at completion -----------------------------------------------------


def test_a_criterion_changed_mid_run_is_flagged_and_its_pass_does_not_settle_it(
    db_session, implementing
):
    ticket, run = implementing
    _edit(db_session, ticket, acceptance_criteria=[NEW, KEPT])

    _finish(db_session, ticket, run)

    [flag] = _flags(db_session, ticket)
    assert flag["run_id"] == run.id
    assert flag["criteria_added"] == [NEW]
    assert flag["criteria_removed"] == [OLD]
    assert flag["dispatched_spec_hash"] == run.spec_hash
    assert run.run_code in flag["reason"]
    # The pass is held: the stage runs again, told the criterion it was not given.
    assert _implement_status(db_session, ticket) is not StageStatus.DONE
    assert ticket.workflow_stage_key == "implement"
    assert NEW in latest_rework_context(db_session, ticket, "implement")
    assert unsettled(db_session, ticket) == [NEW, KEPT]


def test_a_description_or_document_change_is_flagged_and_the_pass_stands(db_session, implementing):
    ticket, run = implementing
    _edit(db_session, ticket, description="Reject empty and blank headers.")
    write_document(db_session, ticket, title="Header rules", body="Blank counts as empty.")

    _finish(db_session, ticket, run)

    [flag] = _flags(db_session, ticket)
    assert flag["description_changed"] is True
    assert flag["documents_added"] == ["Header rules"]
    assert flag["criteria_added"] == []
    assert _implement_status(db_session, ticket) is StageStatus.DONE


def test_a_criterion_only_removed_is_flagged_and_the_pass_stands(db_session, implementing):
    """A pass answers for every criterion still carried; nothing new is left unsettled."""
    ticket, run = implementing
    _edit(db_session, ticket, acceptance_criteria=[KEPT])

    _finish(db_session, ticket, run)

    [flag] = _flags(db_session, ticket)
    assert flag["criteria_removed"] == [OLD]
    assert flag["criteria_added"] == []
    assert _implement_status(db_session, ticket) is StageStatus.DONE


def test_an_unchanged_spec_is_not_flagged(db_session, implementing):
    ticket, run = implementing

    _finish(db_session, ticket, run)

    assert _flags(db_session, ticket) == []
    assert _implement_status(db_session, ticket) is StageStatus.DONE


def test_a_run_with_no_recorded_spec_is_not_flagged(db_session, implementing):
    """Runs from before the column, or a path that built no prompt: no record, not a change."""
    ticket, run = implementing
    run.spec_snapshot_json = run.spec_hash = ""
    db_session.add(run)
    db_session.commit()
    _edit(db_session, ticket, acceptance_criteria=[NEW, KEPT])

    _finish(db_session, ticket, run)

    assert _flags(db_session, ticket) == []


# ---- the run's own edits -------------------------------------------------------


def _own_update(session: Session, ticket: Ticket, run: AgentRun, criteria: list[str]) -> None:
    execute_tool(
        session,
        "loregarden_update_ticket",
        {"ticket_id": ticket.id, "acceptance_criteria": criteria},
        run_id=run.id,
    )
    session.refresh(ticket)
    session.refresh(run)


def test_a_runs_own_criteria_edit_is_not_a_change_under_it(db_session, implementing):
    ticket, run = implementing

    _own_update(db_session, ticket, run, [NEW, KEPT])
    _finish(db_session, ticket, run)

    assert recorded_spec(run).criteria == (NEW, KEPT)
    assert _flags(db_session, ticket) == []
    assert _implement_status(db_session, ticket) is StageStatus.DONE


def test_a_runs_own_edit_after_someone_elses_keeps_the_change_reported(db_session, implementing):
    ticket, run = implementing
    _edit(db_session, ticket, description="Changed by the operator.")

    _own_update(db_session, ticket, run, [NEW, KEPT])
    _finish(db_session, ticket, run)

    [flag] = _flags(db_session, ticket)
    assert flag["description_changed"] is True
    assert flag["criteria_added"] == [NEW]


def test_an_edit_from_a_run_that_is_not_running_moves_no_baseline(db_session, implementing):
    ticket, run = implementing
    run.status = RunStatus.SUCCEEDED
    db_session.add(run)
    db_session.commit()
    dispatched = run.spec_hash

    _own_update(db_session, ticket, run, [NEW, KEPT])

    assert run.spec_hash == dispatched


# ---- what a drift names --------------------------------------------------------


def test_a_new_version_of_a_document_reads_as_rewritten():
    before = SpecSnapshot(description_sha="d", criteria=(OLD,), documents={"a1": "Plan"})
    after = SpecSnapshot(description_sha="d", criteria=(OLD,), documents={"a2": "Plan"})

    drift = spec_drift(before, after)

    assert drift is not None
    assert drift.documents_changed == ("Plan",)
    assert drift.documents_added == drift.documents_removed == ()
    assert not drift.unsettles_criteria
    assert spec_drift(before, before) is None


def test_the_hash_does_not_depend_on_document_order():
    one = SpecSnapshot(description_sha="d", criteria=(), documents={"a": "A", "b": "B"})
    other = SpecSnapshot(description_sha="d", criteria=(), documents={"b": "B", "a": "A"})
    assert one.digest() == other.digest()


# ---- a rework round does not resume a session that read the old spec ----------


def test_a_rework_round_does_not_resume_a_session_given_another_spec(db_session, tmp_path):
    ticket = make_workspace_ticket(db_session, "spec-resume")
    repo_root = Path(tmp_path)
    rework_run(
        db_session,
        ticket,
        repo_root=repo_root,
        code="run_1",
        stdout=_stream(),
        minutes_ago=10,
        spec_hash="old",
    )
    _reject(db_session, ticket)
    changed = rework_run(
        db_session, ticket, repo_root=repo_root, code="run_2", status=RunStatus.RUNNING
    )
    changed.spec_hash = "new"

    plan = _plan(db_session, ticket, changed, repo_root, adapter=CliAdapter.CURSOR)

    assert plan is not None and not plan.resumes
    assert "the spec changed since the earlier run run_1 read it" in plan.skip_reason
