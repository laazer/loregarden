"""A workflow agent asks the operator instead of assuming, and keeps working.

lg-conversation-that-2710. Before it, a stage's "would have asked" went into a
checkpoint file nobody read: lg-code-knowledge-1055's implementer rewrote what
a landed test checks at medium-high confidence, and the only record was a vault
file no inbox, ticket panel or gate looks at. These tests pin the replacement:
the question is an inbox row with the agent's default, a floor decides when it
must stop the stage, an override sends the affected stage back, and the gate
holds until the questions are answered.
"""

from __future__ import annotations

import itertools
import json
from unittest.mock import patch
from uuid import uuid4

import pytest
from loregarden.mcp.tool_ids import AUTO_APPROVED_MCP_TOOLS, STAGE_DEFAULT_MCP_TOOLS, McpTool
from loregarden.mcp.tools import execute_tool
from loregarden.models.domain import (
    AgentRun,
    Approval,
    ApprovalKind,
    ApprovalStatus,
    Artifact,
    ArtifactKind,
    BlockKind,
    OrchestrationRun,
    OrchestrationRunStatus,
    QuestionBlockReason,
    QuestionConfidence,
    QuestionReversibility,
    RunStatus,
    StageReportStatus,
    StageStatus,
    StageType,
    Ticket,
    TicketState,
    WorkflowInstance,
    WorkflowStageDef,
    WorkflowTemplate,
    WorkItemType,
    Workspace,
)
from loregarden.services.operations import (
    Caller,
    OperationId,
    OperationUnavailable,
    Transport,
    run_operation,
)
from loregarden.services.operations.operator_questions import question_row
from loregarden.services.operator_questions import (
    AskRequest,
    apply_queued_overrides,
    ask_operator,
    block_reasons,
    gate_holding_questions,
    queued_behind,
    queued_overrides,
    settled_answer,
)
from loregarden.services.orchestration import ApprovalService, OrchestrationService
from loregarden.services.queue_repair import awaiting_a_person
from loregarden.services.queued_override_invariant import (
    apply_stranded_overrides,
    queued_overrides_with_nothing_running,
)
from loregarden.services.readiness_gate import block_if_unready
from loregarden.services.rework_feedback import (
    MAX_REWORK_REROUTES,
    record_rework_feedback,
    render_rework_feedback,
    rework_reroute_count,
)
from loregarden.services.stage_report import parse_stage_report
from loregarden.services.stage_verdict import collect_signals, derive_verdict
from loregarden.services.unasked_awaits import refuse_unasked_awaits
from sqlmodel import Session, select

SPEC, IMPLEMENT, GATE, DONE = "spec", "implement", "gate", "done"
STAGES = [
    WorkflowStageDef(key=SPEC, name="Spec", order=1, agent_id="spec"),
    WorkflowStageDef(key=IMPLEMENT, name="Implement", order=2, agent_id="backend_implementer"),
    WorkflowStageDef(
        key=GATE, name="Gate", order=3, agent_id="gatekeeper", stage_type=StageType.GATE.value
    ),
    WorkflowStageDef(key=DONE, name="Done", order=4, terminal=True),
]
QUESTION = "May the implementer edit a test an earlier stage landed?"
OPTIONS = ["Fix the test's proxy", "Leave the test red"]


def _stage_map(**status: StageStatus) -> str:
    return json.dumps(
        [{"key": s.key, "status": status.get(s.key, StageStatus.PENDING).value} for s in STAGES]
    )


@pytest.fixture(name="ticket")
def ticket_fixture(db_session: Session, tmp_path) -> Ticket:
    workspace = Workspace(slug=f"oq-{uuid4()}", name="OQ", repo_path=str(tmp_path))
    db_session.add(workspace)
    db_session.commit()
    template = WorkflowTemplate(
        slug=f"oq-tpl-{uuid4()}",
        name="OQ",
        stages_json=json.dumps([s.model_dump(mode="json") for s in STAGES]),
        transitions_json=json.dumps(
            [
                {"from": SPEC, "to": IMPLEMENT, "when": "pass"},
                {"from": IMPLEMENT, "to": GATE, "when": "pass"},
                {"from": GATE, "to": DONE, "when": "pass"},
            ]
        ),
    )
    db_session.add(template)
    db_session.commit()
    ticket = Ticket(
        external_id=f"oq-{uuid4()}",
        workspace_id=workspace.id,
        title="Asserted-not-proven debt class",
        state=TicketState.IN_PROGRESS,
        work_item_type=WorkItemType.TASK,
        workflow_stage_key=IMPLEMENT,
        workflow_stage_status=StageStatus.RUNNING,
    )
    db_session.add(ticket)
    db_session.commit()
    db_session.add(
        WorkflowInstance(
            ticket_id=ticket.id,
            template_id=template.id,
            current_stage_key=IMPLEMENT,
            stages_json=_stage_map(spec=StageStatus.DONE, implement=StageStatus.RUNNING),
        )
    )
    db_session.commit()
    db_session.refresh(ticket)
    return ticket


def _request(**overrides) -> AskRequest:
    fields = {
        "question": QUESTION,
        "options": OPTIONS,
        "default": OPTIONS[0],
        "confidence": QuestionConfidence.MEDIUM,
        "reversibility": QuestionReversibility.EASY,
        "affects": IMPLEMENT,
        "stage_key": IMPLEMENT,
    }
    fields.update(overrides)
    return AskRequest(**fields)


def _running_run(session: Session, ticket: Ticket, stage_key: str = IMPLEMENT) -> AgentRun:
    run = AgentRun(
        run_code=f"r_{uuid4().hex[:6]}",
        ticket_id=ticket.id,
        workspace_id=ticket.workspace_id,
        agent_id="backend_implementer",
        stage_key=stage_key,
        status=RunStatus.RUNNING,
    )
    session.add(run)
    session.commit()
    session.refresh(run)
    return run


def _finish(session: Session, run: AgentRun) -> None:
    run.status = RunStatus.SUCCEEDED
    session.add(run)
    session.commit()


def _approvals(session: Session, ticket: Ticket, kind: ApprovalKind) -> list[Approval]:
    return list(
        session.exec(
            select(Approval).where(Approval.ticket_id == ticket.id, Approval.kind == kind)
        ).all()
    )


# --- The floor ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("blocking", "confidence", "reversibility", "upstream", "requirements"),
    list(
        itertools.product(
            [False, True],
            list(QuestionConfidence),
            list(QuestionReversibility),
            [False, True],
            [False, True],
        )
    ),
)
def test_every_combination_blocks_exactly_when_the_floor_or_the_agent_says(
    blocking, confidence, reversibility, upstream, requirements
):
    reasons = block_reasons(
        _request(blocking=blocking, confidence=confidence, reversibility=reversibility),
        affects_upstream=upstream,
        affects_requirements=requirements,
    )
    expected = set()
    if blocking:
        expected.add(QuestionBlockReason.AGENT_ASKED)
    if confidence is QuestionConfidence.LOW:
        expected.add(QuestionBlockReason.LOW_CONFIDENCE)
    if reversibility is QuestionReversibility.IRREVERSIBLE:
        expected.add(QuestionBlockReason.IRREVERSIBLE)
    if upstream and requirements:
        expected.add(QuestionBlockReason.CHANGES_REQUIREMENTS)
    assert set(reasons) == expected


# --- Asking -----------------------------------------------------------------


def test_a_non_blocking_question_files_one_inbox_row_and_the_stage_keeps_running(
    db_session, ticket
):
    run = _running_run(db_session, ticket)
    result = ask_operator(db_session, ticket, _request(run_id=run.id))

    assert not result.blocking and not result.existing
    row = result.approval
    assert row.kind is ApprovalKind.OPERATOR_QUESTION
    assert row.status is ApprovalStatus.PENDING
    assert row.stage_key == IMPLEMENT
    # Bound through the payload, so cancelling the asking run cannot reject it.
    assert row.run_id is None
    payload = json.loads(row.tool_input_json)
    assert payload["questions"][0]["question"] == QUESTION
    assert payload["operator_question"]["default"] == OPTIONS[0]
    assert payload["operator_question"]["asked_by_run_id"] == run.id
    db_session.refresh(ticket)
    assert ticket.state is TicketState.IN_PROGRESS
    assert ticket.workflow_stage_status is StageStatus.RUNNING
    # The run carries on past it: nothing that frees a lane for a person may count it.
    assert not awaiting_a_person(db_session, ticket)
    with pytest.raises(ValueError):
        refuse_unasked_awaits(db_session, ticket, [IMPLEMENT])
    assert "Carry on" in result.next_step


def test_asking_the_same_words_again_returns_the_open_question(db_session, ticket):
    first = ask_operator(db_session, ticket, _request())
    again = ask_operator(db_session, ticket, _request())

    assert again.existing and again.approval.id == first.approval.id
    assert len(_approvals(db_session, ticket, ApprovalKind.OPERATOR_QUESTION)) == 1


def test_low_confidence_raises_the_decision_block_and_the_blocked_report_reuses_it(
    db_session, ticket
):
    result = ask_operator(db_session, ticket, _request(confidence=QuestionConfidence.LOW))

    assert result.blocking
    assert result.reasons == (QuestionBlockReason.LOW_CONFIDENCE,)
    assert result.approval.kind is ApprovalKind.BLOCK_DECISION
    assert "blocked_kind `decision`" in result.next_step
    assert not _approvals(db_session, ticket, ApprovalKind.OPERATOR_QUESTION)
    # The agent's blocked report that follows finds the decision open.
    from loregarden.services.block_classification import raise_decision

    again = raise_decision(db_session, ticket, stage_key=IMPLEMENT, message="x", options=OPTIONS)
    assert again.id == result.approval.id


def test_an_answer_that_changes_the_spec_blocks_whatever_the_agent_said(db_session, ticket):
    result = ask_operator(
        db_session, ticket, _request(affects=SPEC, confidence=QuestionConfidence.HIGH)
    )

    assert result.blocking
    assert result.reasons == (QuestionBlockReason.CHANGES_REQUIREMENTS,)


def test_a_second_blocking_question_on_the_stage_says_it_was_not_filed(db_session, ticket):
    first = ask_operator(db_session, ticket, _request(confidence=QuestionConfidence.LOW))
    second = ask_operator(
        db_session,
        ticket,
        _request(question="Rename the module too?", confidence=QuestionConfidence.LOW),
    )

    assert second.displaced and not second.existing
    assert second.approval.id == first.approval.id
    assert "NOT filed" in second.next_step
    assert len(_approvals(db_session, ticket, ApprovalKind.BLOCK_DECISION)) == 1


def test_retrying_a_blocking_question_returns_it_as_already_asked(db_session, ticket):
    first = ask_operator(db_session, ticket, _request(confidence=QuestionConfidence.LOW))
    again = ask_operator(db_session, ticket, _request(confidence=QuestionConfidence.LOW))

    assert again.existing and not again.displaced
    assert again.approval.id == first.approval.id


def _list(session: Session, ticket: Ticket) -> dict:
    return json.loads(
        execute_tool(
            session,
            McpTool.LIST_OPERATOR_QUESTIONS.value,
            {"ticket_id": ticket.external_id, "include_settled": True},
        )
    )


def test_a_blocking_question_is_listed_holding_the_gate_and_its_answer_read_back(
    db_session, ticket
):
    from loregarden.services.block_classification import raise_decision

    raise_decision(
        db_session, ticket, stage_key=SPEC, message="A stage's own block", options=OPTIONS
    )
    asked = ask_operator(db_session, ticket, _request(confidence=QuestionConfidence.LOW)).approval

    listed = _list(db_session, ticket)
    assert listed["total"] == 1, "a decision a stage report raised is not an asked question"
    assert listed["open"] == 1 and listed["holding_gate"] == 1
    row = listed["questions"][0]
    assert row["id"] == asked.id and row["question"] == QUESTION
    assert row["default"] == OPTIONS[0] and row["options"] == OPTIONS

    decision_text = json.loads(asked.tool_input_json)["questions"][0]["question"]
    with patch("loregarden.services.run_service.schedule_orchestration"):
        ApprovalService(db_session).resolve(
            asked.id, approved=True, answers={decision_text: OPTIONS[1]}
        )

    row = _list(db_session, ticket)["questions"][0]
    assert (row["outcome"], row["answer"]) == ("overridden", OPTIONS[1])


@pytest.mark.parametrize(
    ("overrides", "named"),
    [
        ({"default": "Something else"}, "default"),
        ({"affects": GATE}, "comes after"),
        ({"affects": "nowhere"}, "not a stage"),
        ({"options": ["Only one"], "default": "Only one"}, "options"),
        ({"options": ["Same", "Same"], "default": "Same"}, "repeat"),
        ({"question": "   "}, "empty"),
    ],
)
def test_a_question_that_cannot_be_answered_is_refused_naming_why(
    db_session, ticket, overrides, named
):
    with pytest.raises(ValueError, match=named):
        ask_operator(db_session, ticket, _request(**overrides))
    assert not _approvals(db_session, ticket, ApprovalKind.OPERATOR_QUESTION)


# --- Answering -------------------------------------------------------------


def test_confirming_the_default_closes_it_and_moves_nothing(db_session, ticket):
    asked = ask_operator(db_session, ticket, _request()).approval

    with patch("loregarden.services.run_service.schedule_orchestration") as scheduled:
        ApprovalService(db_session).resolve(asked.id, approved=True, answers={QUESTION: OPTIONS[0]})

    db_session.refresh(asked)
    db_session.refresh(ticket)
    assert asked.status is ApprovalStatus.APPROVED
    assert json.loads(asked.response_json)["outcome"] == "confirmed"
    assert ticket.workflow_stage_key == IMPLEMENT
    assert ticket.workflow_stage_status is StageStatus.RUNNING
    assert not scheduled.called
    assert render_rework_feedback(db_session, ticket, IMPLEMENT) == ""


def test_an_override_sends_the_affected_stage_back_with_the_answer_bound(db_session, ticket):
    run = _running_run(db_session, ticket)
    asked = ask_operator(db_session, ticket, _request(run_id=run.id)).approval
    _finish(db_session, run)

    with patch("loregarden.services.run_service.schedule_orchestration") as scheduled:
        ApprovalService(db_session).resolve(asked.id, approved=True, answers={QUESTION: OPTIONS[1]})

    db_session.refresh(ticket)
    db_session.refresh(asked)
    assert json.loads(asked.response_json)["outcome"] == "overridden"
    assert ticket.workflow_stage_key == IMPLEMENT
    assert ticket.workflow_stage_status is StageStatus.PENDING
    feedback = render_rework_feedback(db_session, ticket, IMPLEMENT)
    assert OPTIONS[1] in feedback and "BINDING DECISION" in feedback
    note = db_session.exec(
        select(Artifact).where(
            Artifact.ticket_id == ticket.id,
            Artifact.kind == ArtifactKind.CONTEXT,
            Artifact.title == f"Operator answer — {IMPLEMENT}",
        )
    ).one()
    assert OPTIONS[1] in note.content_json
    assert scheduled.called, "the run resumes on its own after the override"


def _override(session: Session, asked: Approval) -> None:
    ApprovalService(session).resolve(asked.id, approved=True, answers={QUESTION: OPTIONS[1]})


def test_an_override_while_a_stage_runs_is_taken_and_queued_behind_the_run(db_session, ticket):
    run = _running_run(db_session, ticket)
    asked = ask_operator(db_session, ticket, _request(run_id=run.id)).approval

    with patch("loregarden.services.orchestration_resume.schedule_orchestration") as scheduled:
        _override(db_session, asked)

    db_session.refresh(asked)
    db_session.refresh(ticket)
    assert asked.status is ApprovalStatus.APPROVED
    assert settled_answer(asked) == ("overridden", OPTIONS[1])
    assert queued_behind(asked) == IMPLEMENT
    assert question_row(asked)["queued_behind"] == IMPLEMENT
    assert ticket.workflow_stage_status is StageStatus.RUNNING, "the live run is left alone"
    assert render_rework_feedback(db_session, ticket, IMPLEMENT) == ""
    assert not scheduled.called


def test_a_queued_override_is_applied_when_the_run_completes(db_session, ticket):
    run = _running_run(db_session, ticket)
    asked = ask_operator(db_session, ticket, _request(run_id=run.id)).approval
    with patch("loregarden.services.orchestration_resume.schedule_orchestration"):
        _override(db_session, asked)

    with patch("loregarden.services.orchestration_resume.schedule_orchestration") as scheduled:
        OrchestrationService(db_session).complete_run(run, status=RunStatus.FAILED, stderr="boom")

    db_session.refresh(asked)
    db_session.refresh(ticket)
    assert ticket.workflow_stage_key == IMPLEMENT
    assert ticket.workflow_stage_status is StageStatus.PENDING
    feedback = render_rework_feedback(db_session, ticket, IMPLEMENT)
    assert OPTIONS[1] in feedback and "BINDING DECISION" in feedback
    assert queued_behind(asked) == "", "applied once"
    assert queued_overrides(db_session, ticket.id) == []
    assert scheduled.called, "the ticket resumes on the operator's answer"


def test_an_override_past_the_rework_cap_resets_the_budget_and_reroutes(db_session, ticket):
    asked = ask_operator(db_session, ticket, _request()).approval
    for round_ in range(MAX_REWORK_REROUTES):
        record_rework_feedback(
            db_session, ticket, target_stage=IMPLEMENT, from_stage=GATE, context=f"r{round_}"
        )

    with patch("loregarden.services.orchestration_resume.schedule_orchestration") as scheduled:
        _override(db_session, asked)

    db_session.refresh(asked)
    db_session.refresh(ticket)
    assert asked.status is ApprovalStatus.APPROVED
    assert rework_reroute_count(db_session, ticket, IMPLEMENT) == 1, "reset, then this round"
    assert ticket.workflow_stage_status is StageStatus.PENDING
    assert scheduled.called


@pytest.mark.parametrize(
    ("said", "expected"),
    [("", f"Not '{OPTIONS[0]}'"), ("Ask the spec author first", "Ask the spec author first")],
    ids=["bare", "with-words"],
)
def test_a_reject_overrides_the_default(db_session, ticket, said, expected):
    asked = ask_operator(db_session, ticket, _request()).approval

    with patch("loregarden.services.orchestration_resume.schedule_orchestration"):
        ApprovalService(db_session).resolve(asked.id, approved=False, response_text=said)

    db_session.refresh(asked)
    db_session.refresh(ticket)
    outcome, answer = settled_answer(asked)
    assert asked.status is ApprovalStatus.REJECTED
    assert outcome == "overridden" and answer.startswith(expected)
    assert ticket.workflow_stage_status is StageStatus.PENDING
    assert expected in render_rework_feedback(db_session, ticket, IMPLEMENT)


def test_queued_overrides_on_two_stages_land_the_cursor_on_the_earlier(db_session, ticket):
    """A spec-affecting question blocks (the floor), so the earlier stage here is implement."""
    run = _running_run(db_session, ticket, GATE)
    later = ask_operator(db_session, ticket, _request(stage_key=GATE, affects=GATE)).approval
    earlier = ask_operator(
        db_session, ticket, _request(question="Which schema?", stage_key=GATE)
    ).approval
    with patch("loregarden.services.orchestration_resume.schedule_orchestration"):
        _override(db_session, later)
        ApprovalService(db_session).resolve(earlier.id, approved=True, answers=_answer_for(earlier))
    assert {queued_behind(later), queued_behind(earlier)} == {GATE}
    _finish(db_session, run)

    assert apply_queued_overrides(db_session, OrchestrationService(db_session), ticket)

    db_session.refresh(ticket)
    assert ticket.workflow_stage_key == IMPLEMENT
    assert OPTIONS[1] in render_rework_feedback(db_session, ticket, IMPLEMENT)
    assert OPTIONS[1] in render_rework_feedback(db_session, ticket, GATE)


def test_the_invariant_applies_an_override_whose_run_ended_outside_completion(db_session, ticket):
    run = _running_run(db_session, ticket)
    asked = ask_operator(db_session, ticket, _request()).approval
    with patch("loregarden.services.orchestration_resume.schedule_orchestration"):
        _override(db_session, asked)
    assert queued_overrides_with_nothing_running(db_session) == [], "still waiting on the run"
    _finish(db_session, run)  # reaped: no completion hook

    found = queued_overrides_with_nothing_running(db_session)
    assert [violation.subject_id for violation in found] == [asked.id]
    with patch("loregarden.services.orchestration_resume.schedule_orchestration") as scheduled:
        assert apply_stranded_overrides(db_session, found) == 1

    db_session.refresh(ticket)
    assert ticket.workflow_stage_key == IMPLEMENT
    assert ticket.workflow_stage_status is StageStatus.PENDING
    assert scheduled.called
    assert queued_overrides_with_nothing_running(db_session) == []


def test_the_invariant_reports_a_queued_override_on_a_closed_ticket_without_reopening_it(
    db_session, ticket
):
    run = _running_run(db_session, ticket)
    asked = ask_operator(db_session, ticket, _request()).approval
    with patch("loregarden.services.orchestration_resume.schedule_orchestration"):
        _override(db_session, asked)
    _finish(db_session, run)
    ticket.state = TicketState.DONE
    db_session.add(ticket)
    db_session.commit()

    found = queued_overrides_with_nothing_running(db_session)
    assert [violation.subject_id for violation in found] == [asked.id]
    assert apply_stranded_overrides(db_session, found) == 0
    db_session.refresh(ticket)
    assert ticket.state is TicketState.DONE


def test_a_second_answer_is_refused(db_session, ticket):
    asked = ask_operator(db_session, ticket, _request()).approval
    service = ApprovalService(db_session)
    service.resolve(asked.id, approved=True, answers={QUESTION: OPTIONS[0]})

    with pytest.raises(ValueError, match="already resolved"):
        service.resolve(asked.id, approved=True, answers={QUESTION: OPTIONS[1]})


# --- The gate ----------------------------------------------------------------


def _at_gate(session: Session, ticket: Ticket) -> OrchestrationRun:
    instance = session.exec(
        select(WorkflowInstance).where(WorkflowInstance.ticket_id == ticket.id)
    ).one()
    instance.stages_json = _stage_map(spec=StageStatus.DONE, implement=StageStatus.DONE)
    instance.current_stage_key = GATE
    ticket.workflow_stage_key = GATE
    ticket.workflow_stage_status = StageStatus.PENDING
    orch = OrchestrationRun(
        run_code=f"orch_{uuid4().hex[:6]}",
        ticket_id=ticket.id,
        workspace_id=ticket.workspace_id,
        status=OrchestrationRunStatus.RUNNING,
    )
    session.add_all([instance, ticket, orch])
    session.commit()
    session.refresh(orch)
    return orch


def test_the_gate_holds_unspawned_for_an_open_question_and_releases_on_the_last_answer(
    db_session, ticket
):
    asked = ask_operator(db_session, ticket, _request()).approval
    orch = _at_gate(db_session, ticket)

    assert block_if_unready(db_session, orch, ticket)

    db_session.refresh(ticket)
    assert ticket.state is TicketState.BLOCKED
    assert ticket.block_kind is BlockKind.ANSWERS
    assert asked.id in ticket.blocking_issues
    # No card of its own: the question is the card.
    assert not _approvals(db_session, ticket, ApprovalKind.BLOCK_DECISION)
    assert not _approvals(db_session, ticket, ApprovalKind.HUMAN_ACTION)

    orch.status = OrchestrationRunStatus.BLOCKED
    db_session.add(orch)
    db_session.commit()
    with patch("loregarden.services.run_service.schedule_orchestration") as scheduled:
        ApprovalService(db_session).resolve(asked.id, approved=True, answers={QUESTION: OPTIONS[0]})

    db_session.refresh(ticket)
    assert ticket.state is not TicketState.BLOCKED
    assert ticket.block_kind is None
    assert ticket.workflow_stage_key == GATE
    assert ticket.workflow_stage_status is StageStatus.PENDING
    assert scheduled.called


def test_a_high_confidence_question_does_not_hold_the_gate(db_session, ticket):
    ask_operator(db_session, ticket, _request(confidence=QuestionConfidence.HIGH))
    orch = _at_gate(db_session, ticket)

    assert not gate_holding_questions(db_session, ticket)
    assert not block_if_unready(db_session, orch, ticket)


def test_a_gate_pass_with_an_open_question_derives_blocked_on_answers(db_session, ticket):
    ask_operator(db_session, ticket, _request())
    _at_gate(db_session, ticket)
    report = parse_stage_report(
        '<<<LOREGARDEN_STAGE_REPORT>>>\n{"status": "pass", "confidence": 0.9}\n'
        "<<<END_STAGE_REPORT>>>"
    )

    verdict = derive_verdict(
        collect_signals(
            db_session,
            ticket,
            report=report,
            tool_outcome=None,
            stage_def=STAGES[2],
            handoff_expected=True,
            agent_id="gatekeeper",
        )
    )

    assert verdict.status is StageReportStatus.BLOCKED
    assert verdict.block_kind is BlockKind.ANSWERS


def test_an_override_at_the_gate_hold_clears_the_gate_and_reroutes(db_session, ticket):
    asked = ask_operator(db_session, ticket, _request()).approval
    orch = _at_gate(db_session, ticket)
    block_if_unready(db_session, orch, ticket)
    orch.status = OrchestrationRunStatus.BLOCKED
    db_session.add(orch)
    db_session.commit()

    with patch("loregarden.services.run_service.schedule_orchestration"):
        ApprovalService(db_session).resolve(asked.id, approved=True, answers={QUESTION: OPTIONS[1]})

    db_session.refresh(ticket)
    instance = db_session.exec(
        select(WorkflowInstance).where(WorkflowInstance.ticket_id == ticket.id)
    ).one()
    statuses = {row["key"]: row["status"] for row in json.loads(instance.stages_json)}
    assert ticket.workflow_stage_key == IMPLEMENT
    assert statuses[GATE] != StageStatus.BLOCKED.value
    assert ticket.block_kind is None


def test_ignoring_the_last_question_releases_the_gate(db_session, ticket):
    asked = ask_operator(db_session, ticket, _request()).approval
    orch = _at_gate(db_session, ticket)
    block_if_unready(db_session, orch, ticket)
    orch.status = OrchestrationRunStatus.BLOCKED
    db_session.add(orch)
    db_session.commit()

    with patch("loregarden.services.run_service.schedule_orchestration"):
        ApprovalService(db_session).ignore(asked.id, reason="Either is fine for this ticket.")

    db_session.refresh(ticket)
    assert ticket.block_kind is None
    assert ticket.workflow_stage_status is StageStatus.PENDING


def test_an_agent_cannot_declare_the_gates_answers_kind():
    report = parse_stage_report(
        '<<<LOREGARDEN_STAGE_REPORT>>>\n{"status": "blocked", "blocked_kind": "answers"}\n'
        "<<<END_STAGE_REPORT>>>"
    )
    assert report is not None
    assert report.blocked_kind is None


# --- Transports ---------------------------------------------------------------


def test_the_tools_are_offered_to_stages_and_asking_needs_no_click():
    assert McpTool.ASK_OPERATOR in STAGE_DEFAULT_MCP_TOOLS
    assert McpTool.LIST_OPERATOR_QUESTIONS in STAGE_DEFAULT_MCP_TOOLS
    assert McpTool.ASK_OPERATOR in AUTO_APPROVED_MCP_TOOLS
    assert McpTool.LIST_OPERATOR_QUESTIONS in AUTO_APPROVED_MCP_TOOLS


def test_asking_over_mcp_and_listing_it_back(db_session, ticket):
    run = _running_run(db_session, ticket)
    reply = json.loads(
        execute_tool(
            db_session,
            McpTool.ASK_OPERATOR.value,
            {
                "ticket_id": ticket.external_id,
                "question": QUESTION,
                "options": OPTIONS,
                "default": OPTIONS[0],
                "confidence": "medium",
                "reversibility": "costly",
                "affects": IMPLEMENT,
            },
            orchestrated=True,
            run_id=run.id,
        )
    )
    assert reply["blocking"] is False
    listed = json.loads(
        execute_tool(
            db_session, McpTool.LIST_OPERATOR_QUESTIONS.value, {"ticket_id": ticket.external_id}
        )
    )
    assert listed["open"] == 1 and listed["holding_gate"] == 1
    row = listed["questions"][0]
    assert row["id"] == reply["question_id"]
    assert row["default"] == OPTIONS[0] and row["options"] == OPTIONS
    assert row["stage_key"] == IMPLEMENT and row["affects"] == IMPLEMENT


def test_with_no_ticket_the_list_covers_every_open_question_in_the_workspace(db_session, ticket):
    from loregarden.models.domain import Workspace

    asked = ask_operator(db_session, ticket, _request()).approval
    blocking = ask_operator(
        db_session, ticket, _request(question="Drop the column?", confidence=QuestionConfidence.LOW)
    ).approval
    settled = ask_operator(db_session, ticket, _request(question="Rename it?")).approval
    with patch("loregarden.services.run_service.schedule_orchestration"):
        ApprovalService(db_session).resolve(
            settled.id, approved=True, answers={"Rename it?": OPTIONS[0]}
        )
    slug = db_session.get(Workspace, ticket.workspace_id).slug

    listed = json.loads(
        execute_tool(db_session, McpTool.LIST_OPERATOR_QUESTIONS.value, {"workspace_slug": slug})
    )

    assert {row["id"] for row in listed["questions"]} == {asked.id, blocking.id}
    assert listed["open"] == 2 and listed["holding_gate"] == 2
    assert {row["ticket"] for row in listed["questions"]} == {ticket.external_id}


def test_the_list_names_what_it_needs_when_given_neither_ticket_nor_workspace(db_session):
    with pytest.raises(ValueError, match="workspace_slug"):
        execute_tool(db_session, McpTool.LIST_OPERATOR_QUESTIONS.value, {})


def test_an_operator_does_not_ask_over_rest(db_session, ticket):
    with pytest.raises(OperationUnavailable):
        run_operation(
            db_session,
            OperationId.OPERATOR_QUESTION_ASK,
            {"ticket_id": ticket.id},
            Caller(Transport.REST),
        )


# --- A closed ticket ---------------------------------------------------------


def _close(session: Session, ticket: Ticket, state: TicketState = TicketState.DONE) -> None:
    ticket.state = state
    session.add(ticket)
    session.commit()


def _pending_of_kind(session: Session, ticket: Ticket, kind: ApprovalKind) -> Approval:
    """A pending approval of `kind`, shaped as its own writer files it."""
    if kind is ApprovalKind.OPERATOR_QUESTION:
        return ask_operator(session, ticket, _request()).approval
    if kind is ApprovalKind.BLOCK_DECISION:
        from loregarden.services.block_classification import raise_decision

        return raise_decision(
            session, ticket, stage_key=IMPLEMENT, message="Which way?", options=OPTIONS
        )
    approval = Approval(
        ticket_id=ticket.id,
        workspace_id=ticket.workspace_id,
        kind=kind,
        title=f"A {kind.value}",
        stage_key=IMPLEMENT,
    )
    session.add(approval)
    session.commit()
    return approval


def _answer_for(approval: Approval) -> dict[str, str]:
    questions = json.loads(approval.tool_input_json or "{}").get("questions") or []
    return {questions[0]["question"]: OPTIONS[1]} if questions else {}


@pytest.mark.parametrize("approved", [True, False], ids=["approve", "reject"])
@pytest.mark.parametrize("kind", list(ApprovalKind), ids=lambda kind: kind.value)
def test_no_resolve_branch_records_the_row_and_then_fails_the_request(
    db_session, ticket, kind, approved
):
    """lg-conversation-that-2733: a decision answered on a done ticket was
    committed as approved, then the requeue raised `done -> in_progress` and the
    request returned 400. Whatever a branch does, a raise must leave the row
    pending, and a resolved row must come back with no error."""
    approval = _pending_of_kind(db_session, ticket, kind)
    _close(db_session, ticket)

    try:
        with patch("loregarden.services.run_service.schedule_orchestration"):
            ApprovalService(db_session).resolve(
                approval.id, approved=approved, answers=_answer_for(approval) or None
            )
    except ValueError:
        db_session.rollback()
        db_session.refresh(approval)
        assert approval.status is ApprovalStatus.PENDING, "refused, yet the row was recorded"
    else:
        db_session.refresh(approval)
        assert approval.status is not ApprovalStatus.PENDING


@pytest.mark.parametrize("state", [TicketState.DONE, TicketState.WONT_DO])
@pytest.mark.parametrize(
    "kind",
    [ApprovalKind.BLOCK_DECISION, ApprovalKind.OPERATOR_QUESTION, ApprovalKind.STAGE_PARK],
    ids=lambda k: k.value,
)
def test_an_answer_on_a_closed_ticket_is_refused_and_stays_pending(db_session, ticket, kind, state):
    approval = _pending_of_kind(db_session, ticket, kind)
    _close(db_session, ticket, state)

    with pytest.raises(ValueError, match=f"{state.value}.*[Rr]eopen"):
        ApprovalService(db_session).resolve(
            approval.id, approved=True, answers=_answer_for(approval)
        )

    db_session.refresh(approval)
    db_session.refresh(ticket)
    assert approval.status is ApprovalStatus.PENDING
    assert ticket.state is state, "a refused answer must not reopen the ticket"


def test_the_same_answer_on_an_open_ticket_requeues_the_stage(db_session, ticket):
    approval = _pending_of_kind(db_session, ticket, ApprovalKind.BLOCK_DECISION)

    with patch("loregarden.services.run_service.schedule_orchestration"):
        ApprovalService(db_session).resolve(
            approval.id, approved=True, answers=_answer_for(approval)
        )

    db_session.refresh(approval)
    db_session.refresh(ticket)
    assert approval.status is ApprovalStatus.APPROVED
    assert ticket.state is TicketState.IN_PROGRESS
    assert ticket.workflow_stage_key == IMPLEMENT


@pytest.mark.parametrize(
    "kind", [ApprovalKind.BLOCK_DECISION, ApprovalKind.OPERATOR_QUESTION], ids=lambda k: k.value
)
def test_ignoring_a_question_on_a_closed_ticket_closes_it_and_leaves_the_ticket_closed(
    db_session, ticket, kind
):
    """Ignore is how a stale card on a closed ticket is cleared, so it must not
    be refused, and it must not try to move the ticket either."""
    approval = _pending_of_kind(db_session, ticket, kind)
    _close(db_session, ticket)

    ApprovalService(db_session).ignore(approval.id, reason="The ticket shipped without it.")

    db_session.refresh(approval)
    db_session.refresh(ticket)
    assert approval.status is ApprovalStatus.IGNORED
    assert ticket.state is TicketState.DONE


def test_the_invariant_finds_an_answer_recorded_after_its_ticket_closed(db_session, ticket):
    from datetime import timedelta

    from loregarden.models.domain.enums import utcnow
    from loregarden.services.closed_ticket_answers import answers_recorded_on_closed_tickets

    answered = _pending_of_kind(db_session, ticket, ApprovalKind.BLOCK_DECISION)
    ignored = _pending_of_kind(db_session, ticket, ApprovalKind.OPERATOR_QUESTION)
    closed_at = utcnow()
    ticket.state, ticket.resolved_at = TicketState.DONE, closed_at
    answered.status, answered.resolved_at = (
        ApprovalStatus.APPROVED,
        closed_at + timedelta(minutes=1),
    )
    ignored.status, ignored.resolved_at = ApprovalStatus.IGNORED, closed_at + timedelta(minutes=1)
    db_session.add_all([ticket, answered, ignored])
    db_session.commit()

    found = answers_recorded_on_closed_tickets(db_session)

    assert [violation.subject_id for violation in found] == [answered.id]
