"""A bug ticket's spec stage cannot pass without deciding how the fix covers the whole state.

The four answers are recorded with `loregarden_record_fix_coverage`, which
writes the learning to memory and validates by the PR check's own shape rules.
A spec pass on a bug with no record is sent back to the stage, under the
rework loop cap.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from uuid import uuid4

import pytest
from loregarden.agents.registry import SPEC_AGENT_ID
from loregarden.mcp.fix_coverage_tools import HANDLERS
from loregarden.models.domain import (
    AgentRun,
    OrchestrationRun,
    OrchestrationRunStatus,
    RunStatus,
    StageStatus,
    Ticket,
    TicketState,
    WorkflowInstance,
    WorkflowStageDef,
    WorkflowTemplate,
    WorkItemType,
    Workspace,
)
from loregarden.services.fix_coverage import FixCoverage, latest_fix_coverage
from loregarden.services.memory_store import AgentMemoryService
from loregarden.services.orchestration import OrchestrationService
from loregarden.services.rework_feedback import MAX_REWORK_REROUTES, render_rework_feedback
from loregarden.services.workflow_state import initial_stages_json, parse_stage_map
from pydantic import ValidationError
from sqlmodel import Session

_ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location(
    "pr_fix_coverage_check", _ROOT / ".github" / "scripts" / "pr_fix_coverage_check.py"
)
pr_check = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(pr_check)

SPEC = "spec"
IMPLEMENT = "implement"
STAGES = [
    WorkflowStageDef(key="plan", name="Plan", order=1, agent_id="planner"),
    WorkflowStageDef(key=SPEC, name="Spec", order=2, agent_id=SPEC_AGENT_ID),
    WorkflowStageDef(key=IMPLEMENT, name="Implement", order=3, agent_id="backend_implementer"),
    WorkflowStageDef(key="done", name="Done", order=4, terminal=True),
]

ANSWERS = {
    "existing": "0 rows: `select count(*) from tickets where block_kind is null` on live",
    "reentry": "`every_block_is_settled` reports a block written around settle_block",
    "prevention": "server/tests/test_invariants.py::test_invariant_names_are_unique",
    "learning": "A block written outside settle_block reads as handled when nothing handled it",
}


def _ticket(session: Session, tmp_path, work_item_type: WorkItemType) -> Ticket:
    workspace = Workspace(slug=f"fix-{uuid4()}", name="Fix", repo_path=str(tmp_path))
    template = WorkflowTemplate(
        slug=f"fix-tpl-{uuid4()}",
        name="Fix",
        stages_json=json.dumps([s.model_dump(mode="json") for s in STAGES]),
        transitions_json=json.dumps(
            [
                {"from": "plan", "to": SPEC, "when": "pass"},
                {"from": SPEC, "to": IMPLEMENT, "when": "pass"},
                {"from": IMPLEMENT, "to": "done", "when": "pass"},
            ]
        ),
    )
    session.add(template)
    session.commit()
    workspace.workflow_template_id = template.id
    session.add(workspace)
    session.commit()
    ticket = Ticket(
        external_id=f"fix-{uuid4().hex[:8]}",
        workspace_id=workspace.id,
        title="Blocks written around settle_block look handled",
        state=TicketState.IN_PROGRESS,
        work_item_type=work_item_type,
        workflow_stage_key=SPEC,
        workflow_stage_status=StageStatus.RUNNING,
    )
    session.add(ticket)
    session.commit()
    session.refresh(ticket)
    session.add(
        WorkflowInstance(
            ticket_id=ticket.id,
            template_id=template.id,
            current_stage_key=SPEC,
            stages_json=initial_stages_json(STAGES),
        )
    )
    session.commit()
    return ticket


@pytest.fixture(name="bug")
def bug_fixture(db_session: Session, tmp_path) -> Ticket:
    return _ticket(db_session, tmp_path, WorkItemType.BUG)


def _passes(session: Session, ticket: Ticket, *, agent_id: str = SPEC_AGENT_ID) -> None:
    parent = OrchestrationRun(
        run_code=f"orch_{uuid4().hex[:6]}",
        ticket_id=ticket.id,
        workspace_id=ticket.workspace_id,
        status=OrchestrationRunStatus.RUNNING,
    )
    session.add(parent)
    session.commit()
    run = AgentRun(
        run_code=f"r_{uuid4().hex[:6]}",
        ticket_id=ticket.id,
        workspace_id=ticket.workspace_id,
        agent_id=agent_id,
        stage_key=SPEC,
        status=RunStatus.RUNNING,
        orchestration_run_id=parent.id,
    )
    session.add(run)
    session.commit()
    session.refresh(run)
    report = json.dumps({"status": "pass", "confidence": 0.9})
    OrchestrationService(session).complete_run(
        run,
        status=RunStatus.SUCCEEDED,
        stdout=f"<<<LOREGARDEN_STAGE_REPORT>>>\n{report}\n<<<END_STAGE_REPORT>>>\n",
    )
    parent.status = OrchestrationRunStatus.BLOCKED
    session.add(parent)
    session.commit()
    session.refresh(ticket)


def _spec_status(session: Session, ticket: Ticket) -> StageStatus:
    instance, stages = OrchestrationService(session)._resolve_stages(ticket)
    return parse_stage_map(instance, stages)[SPEC]


def _record(session: Session, ticket: Ticket, **answers: str) -> dict:
    return json.loads(
        HANDLERS["loregarden_record_fix_coverage"](
            session, {"ticket_id": ticket.external_id, **answers}
        )
    )


# ---- the spec stage --------------------------------------------------------


def test_a_bug_spec_pass_with_no_record_goes_back_to_the_spec_stage(db_session, bug):
    _passes(db_session, bug)

    assert _spec_status(db_session, bug) is StageStatus.PENDING, "re-armed, not passed"
    told = render_rework_feedback(db_session, bug, SPEC)
    assert "loregarden_record_fix_coverage" in told, told
    assert bug.next_agent == SPEC_AGENT_ID


def test_a_recorded_fix_coverage_lets_the_spec_stage_pass(db_session, bug):
    _record(db_session, bug, **ANSWERS)

    _passes(db_session, bug)

    assert _spec_status(db_session, bug) is StageStatus.DONE


def test_a_recorded_waiver_is_an_answer(db_session, bug):
    _record(db_session, bug, not_a_state_bug="A wrong label on a toast; nothing is persisted")

    _passes(db_session, bug)

    assert _spec_status(db_session, bug) is StageStatus.DONE


@pytest.mark.parametrize("work_item_type", [WorkItemType.FEATURE, WorkItemType.TASK])
def test_a_ticket_that_is_not_a_bug_is_not_asked(db_session, tmp_path, work_item_type):
    ticket = _ticket(db_session, tmp_path, work_item_type)

    _passes(db_session, ticket)

    assert _spec_status(db_session, ticket) is StageStatus.DONE


def test_other_agents_are_not_asked(db_session, bug):
    _passes(db_session, bug, agent_id="planner")

    assert _spec_status(db_session, bug) is StageStatus.DONE


def test_a_spec_stage_that_never_records_it_stops_at_the_loop_cap(db_session, bug):
    for _ in range(MAX_REWORK_REROUTES + 1):
        _passes(db_session, bug)

    assert _spec_status(db_session, bug) is StageStatus.BLOCKED
    assert bug.state is TicketState.BLOCKED


# ---- the record --------------------------------------------------------------


def test_recording_writes_the_learning_to_memory_first(db_session, bug):
    result = _record(db_session, bug, **ANSWERS)

    assert result["learning"]["graph"]["title"].startswith("A block written outside settle_block")
    workspace = db_session.get(Workspace, bug.workspace_id)
    found = AgentMemoryService.from_settings().search("settle_block", workspace_slug=workspace.slug)
    assert found, "the learning must be readable where the next agent searches"
    assert latest_fix_coverage(db_session, bug) == FixCoverage(**ANSWERS)


def test_a_learning_that_cannot_be_written_leaves_no_record(db_session, bug, monkeypatch):
    def refuse(*_args, **_kwargs):
        raise ValueError("no memory graph for this workspace")

    monkeypatch.setattr(AgentMemoryService, "append_learning", refuse)

    with pytest.raises(ValueError, match="no memory graph"):
        _record(db_session, bug, **ANSWERS)
    assert latest_fix_coverage(db_session, bug) is None


def test_a_bad_record_is_refused_with_every_reason(db_session, bug):
    with pytest.raises(ValueError, match="not recorded") as refused:
        _record(db_session, bug, **{**ANSWERS, "reentry": "the reconciler will notice"})
    assert "names no invariant" in str(refused.value)
    assert latest_fix_coverage(db_session, bug) is None


# ---- the record and the PR check agree ---------------------------------------

CASES = [
    ANSWERS,
    {**ANSWERS, "existing": "<how>"},
    {**ANSWERS, "learning": "short"},
    {**ANSWERS, "reentry": "the reconciler will notice it"},
    {**ANSWERS, "reentry": "no detector: the state lives in git refs nothing scans"},
    {**ANSWERS, "reentry": "no detector: n/a lol"},
    {**ANSWERS, "prevention": "the new guard in run_completion covers it"},
    {"not_a_state_bug": "A wrong label on a toast; nothing is persisted"},
    {"not_a_state_bug": "typo"},
]


@pytest.mark.parametrize("answers", CASES)
def test_the_record_validates_exactly_what_the_pr_check_accepts(answers):
    """The record's lines are what a tree PR's section carries, so a record the
    check would reject must be refused when it is written, not found in CI."""
    try:
        lines = FixCoverage(**answers).lines
    except ValidationError:
        lines = None
    if lines is None:
        body = "## Fix coverage\n" + "\n".join(
            f"- **{label}:** {answers.get(key, '')}"
            for key, label in (
                ("existing", "Existing"),
                ("reentry", "Re-entry"),
                ("prevention", "Prevention"),
                ("learning", "Learning"),
            )
        )
        if "not_a_state_bug" in answers:
            body = f"## Fix coverage\n- **Not a state bug:** {answers['not_a_state_bug']}\n"
        assert pr_check.problems("fix: x", body, _ROOT) != []
    else:
        body = "## Fix coverage\n" + "\n".join(lines) + "\n"
        assert pr_check.problems("fix: x", body, _ROOT) == []
