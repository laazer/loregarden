"""Every block has a kind, and the kinds a person need not see never reach one.

Before 749 a lease expiry, a test the agent could not crack, and a spec bar
that tripped were the same `blocked` state, and each cost the same three
turns: ask what happened, ask for the fix, requeue by hand. The kind is what
lets the control plane act on the first two itself and turn the third into a
question with options whose answer requeues the ticket on its own.
"""

from __future__ import annotations

import json
from unittest.mock import patch
from uuid import uuid4

import pytest
from loregarden.core.event_bus import event_bus
from loregarden.models.domain import (
    AgentRun,
    Approval,
    ApprovalKind,
    ApprovalStatus,
    Artifact,
    ArtifactKind,
    BlockKind,
    EventType,
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
from loregarden.services.block_classification import (
    classify_block_message,
    record_block,
    sweep_unclassified_blocks,
)
from loregarden.services.interruption_messages import (
    DISPATCH_REFUSED_TERMINAL_PARENT_PREFIX,
    INTERRUPTED_RUN_MESSAGE,
    ORPHAN_OF_TERMINAL_ORCH_MESSAGE,
    STRANDED_STAGE_MESSAGE,
)
from loregarden.services.orchestration import ApprovalService, OrchestrationService
from loregarden.services.run_service import AGENT_LEASE_EXPIRED_MESSAGE, EXPIRED_LEASE_MESSAGE
from loregarden.services.workflow_state import initial_stages_json
from sqlmodel import Session, select

IMPLEMENT = "implement"
STAGES = [
    WorkflowStageDef(key="spec", name="Spec", order=1, agent_id="spec"),
    WorkflowStageDef(key=IMPLEMENT, name="Implement", order=2, agent_id="implementation_frontend"),
    WorkflowStageDef(key="done", name="Done", order=3, terminal=True),
]

SDF_39_MESSAGE = (
    "Displaced icosphere package landed and 88/90 ACs green. AC-R6-03 spike fail bars "
    "tripped: lizard far_surface_fraction=0.490, dragon=0.785 (>0.20). Escalating per spec."
)
SDF_39_OPTIONS = ["Relax the bar to 0.5", "Try approach B", "Accept and continue"]


@pytest.mark.parametrize(
    "message",
    [
        INTERRUPTED_RUN_MESSAGE,
        STRANDED_STAGE_MESSAGE,
        ORPHAN_OF_TERMINAL_ORCH_MESSAGE,
        EXPIRED_LEASE_MESSAGE,
        AGENT_LEASE_EXPIRED_MESSAGE,
        "Agent run exited successfully but emitted no parseable <<<LOREGARDEN_STAGE_REPORT>>> block.",
        "Environment preflight failed before this stage could run.\n  git_core_bare: ...",
        "Run run_abc — usage limit reached (Pro usage limit)",
        # lg-bug-hole-574, 2026-09-16 23:48: the stage passed, its parent had
        # been failed under it, and this refusal was filed as work.
        f"{DISPATCH_REFUSED_TERMINAL_PARENT_PREFIX} orch_b1622f is failed",
    ],
)
def test_the_control_plane_names_its_own_failures_harness(message):
    assert classify_block_message(message) is BlockKind.HARNESS


def test_a_block_asking_for_a_person_is_human_action():
    assert classify_block_message("Needs a person to plug the device in.") is BlockKind.HUMAN_ACTION


def test_an_unexplained_block_is_work():
    assert classify_block_message("Two golden tests still red after three tries.") is BlockKind.WORK


@pytest.fixture(name="ticket")
def ticket_fixture(db_session: Session, tmp_path) -> Ticket:
    workspace = Workspace(slug=f"bk-{uuid4()}", name="BK", repo_path=str(tmp_path))
    db_session.add(workspace)
    db_session.commit()
    db_session.refresh(workspace)
    template = WorkflowTemplate(
        slug=f"bk-tpl-{uuid4()}",
        name="BK",
        stages_json=json.dumps([s.model_dump(mode="json") for s in STAGES]),
        transitions_json=json.dumps([{"from": "spec", "to": IMPLEMENT, "when": "pass"}]),
    )
    db_session.add(template)
    db_session.commit()
    db_session.refresh(template)
    ticket = Ticket(
        external_id=f"bk-{uuid4()}",
        workspace_id=workspace.id,
        title="Spike the cage",
        state=TicketState.IN_PROGRESS,
        work_item_type=WorkItemType.TASK,
        workflow_stage_key=IMPLEMENT,
        workflow_stage_status=StageStatus.RUNNING,
    )
    db_session.add(ticket)
    db_session.commit()
    db_session.refresh(ticket)
    db_session.add(
        WorkflowInstance(
            ticket_id=ticket.id,
            template_id=template.id,
            current_stage_key=IMPLEMENT,
            stages_json=initial_stages_json(STAGES),
        )
    )
    db_session.commit()
    return ticket


def _blocked_report(kind: str | None, options: list[str] | None = None) -> str:
    payload: dict = {
        "status": "blocked",
        "confidence": 0.92,
        "reroute_context": SDF_39_MESSAGE,
        "blocked_kind": kind,
    }
    if options is not None:
        payload["options"] = options
    return f"<<<LOREGARDEN_STAGE_REPORT>>>\n{json.dumps(payload)}\n<<<END_STAGE_REPORT>>>\n"


def _complete_blocked(session: Session, ticket: Ticket, stdout: str) -> None:
    parent = OrchestrationRun(
        run_code=f"orch_{uuid4().hex[:6]}",
        ticket_id=ticket.id,
        workspace_id=ticket.workspace_id,
        status=OrchestrationRunStatus.RUNNING,
    )
    session.add(parent)
    session.commit()
    session.refresh(parent)
    run = AgentRun(
        run_code=f"r_{uuid4().hex[:6]}",
        ticket_id=ticket.id,
        workspace_id=ticket.workspace_id,
        agent_id="implementation_frontend",
        stage_key=IMPLEMENT,
        status=RunStatus.RUNNING,
        orchestration_run_id=parent.id,
    )
    session.add(run)
    session.commit()
    session.refresh(run)
    OrchestrationService(session).complete_run(run, status=RunStatus.SUCCEEDED, stdout=stdout)
    # The driver ends its run when the stage blocks; a resume needs no live run.
    parent.status = OrchestrationRunStatus.BLOCKED
    session.add(parent)
    session.commit()


def _decisions(session: Session, ticket: Ticket) -> list[dict]:
    return [
        json.loads(e.payload_json or "{}")
        for e in event_bus.ticket_history(session, ticket.id)
        if e.type == EventType.ORCHESTRATOR_DECISION
    ]


def _pending_decision(session: Session, ticket: Ticket) -> Approval | None:
    return session.exec(
        select(Approval).where(
            Approval.ticket_id == ticket.id,
            Approval.kind == ApprovalKind.BLOCK_DECISION,
            Approval.status == ApprovalStatus.PENDING,
        )
    ).first()


def test_a_decision_block_becomes_one_question_with_the_agents_options(db_session, ticket):
    """The sdf-39 shape: 'escalating per spec' is a decision, and a person
    should get a question with choices, not a dead ticket to interrogate."""
    _complete_blocked(db_session, ticket, _blocked_report("decision", SDF_39_OPTIONS))

    db_session.refresh(ticket)
    assert ticket.block_kind is BlockKind.DECISION
    approval = _pending_decision(db_session, ticket)
    assert approval is not None
    question = json.loads(approval.tool_input_json)["questions"][0]
    assert question["question"] == SDF_39_MESSAGE
    assert [o["label"] for o in question["options"]] == SDF_39_OPTIONS
    classified = [d for d in _decisions(db_session, ticket) if d["decision"] == "classified_block"]
    assert classified and classified[0]["block_kind"] == "decision"


def test_answering_the_question_requeues_the_stage_with_no_manual_step(db_session, ticket):
    _complete_blocked(db_session, ticket, _blocked_report("decision", SDF_39_OPTIONS))
    approval = _pending_decision(db_session, ticket)
    assert approval is not None

    with patch("loregarden.services.run_service.schedule_orchestration") as scheduled:
        ApprovalService(db_session).resolve(
            approval.id, approved=True, answers={SDF_39_MESSAGE: "Try approach B"}
        )

    db_session.refresh(ticket)
    # Not blocked; the exact state is derived (backlog until the scheduled
    # resume below starts the run), and that derivation is not under test here.
    assert ticket.state is not TicketState.BLOCKED
    assert ticket.workflow_stage_key == IMPLEMENT
    assert ticket.workflow_stage_status is StageStatus.PENDING
    assert ticket.blocking_issues == ""
    assert ticket.block_kind is None
    # The choice is on the ticket where the rerun will read it.
    decision_note = db_session.exec(
        select(Artifact).where(
            Artifact.ticket_id == ticket.id,
            Artifact.kind == ArtifactKind.CONTEXT,
            Artifact.title == f"Decision — {IMPLEMENT}",
        )
    ).one()
    assert "Try approach B" in decision_note.content_json
    assert scheduled.called, "the run resumes itself after the answer, as a gate would"
    kinds = [d["decision"] for d in _decisions(db_session, ticket)]
    assert "requeued_after_decision" in kinds


def test_rejecting_the_question_keeps_the_block_with_the_persons_words(db_session, ticket):
    _complete_blocked(db_session, ticket, _blocked_report("decision", SDF_39_OPTIONS))
    approval = _pending_decision(db_session, ticket)
    assert approval is not None

    ApprovalService(db_session).resolve(
        approval.id, approved=False, response_text="Neither; rethink the cage."
    )

    db_session.refresh(ticket)
    assert ticket.blocking_issues == "Neither; rethink the cage."
    assert ticket.workflow_stage_status is StageStatus.BLOCKED


def test_an_unnamed_kind_is_work_and_the_history_says_the_agent_did_not_say(db_session, ticket):
    _complete_blocked(db_session, ticket, _blocked_report(None))

    db_session.refresh(ticket)
    assert ticket.block_kind is BlockKind.WORK
    assert _pending_decision(db_session, ticket) is None
    classified = [d for d in _decisions(db_session, ticket) if d["decision"] == "classified_block"]
    assert "named no kind" in classified[0]["reason"]


def test_the_sweep_classifies_blocks_the_writers_did_not(db_session, ticket):
    """~30 block writers predate the kind; the reconciler makes the rule true."""
    ticket.blocking_issues = AGENT_LEASE_EXPIRED_MESSAGE
    ticket.workflow_stage_status = StageStatus.BLOCKED
    db_session.add(ticket)
    db_session.commit()

    assert sweep_unclassified_blocks(db_session) == 1
    db_session.refresh(ticket)
    assert ticket.block_kind is BlockKind.HARNESS
    assert sweep_unclassified_blocks(db_session) == 0  # idempotent


def test_record_block_does_not_raise_a_second_question_for_the_same_stage(db_session, ticket):
    record_block(
        db_session,
        ticket,
        stage_key=IMPLEMENT,
        message="pick one",
        declared=BlockKind.DECISION,
        options=["a", "b"],
    )
    record_block(
        db_session,
        ticket,
        stage_key=IMPLEMENT,
        message="pick one",
        declared=BlockKind.DECISION,
        options=["a", "b"],
    )

    pending = db_session.exec(
        select(Approval).where(
            Approval.ticket_id == ticket.id, Approval.kind == ApprovalKind.BLOCK_DECISION
        )
    ).all()
    assert len(pending) == 1


def test_the_sweep_reads_the_error_artifact_behind_an_errors_tab_pointer(db_session, ticket):
    """A long block leaves only a pointer inline; classifying the pointer would
    call every such block `work`. The sdf-39 shape: the real message names a
    person."""
    from loregarden.services.artifact_service import record_blocking_issue

    long_message = "Needs a person to choose: " + "x" * 600
    ticket.blocking_issues = record_blocking_issue(
        db_session, ticket, run_id=None, stage_key=IMPLEMENT, message=long_message
    )
    ticket.workflow_stage_status = StageStatus.BLOCKED
    db_session.add(ticket)
    db_session.commit()
    assert "see the Errors tab" in ticket.blocking_issues  # the pointer, not the words

    assert sweep_unclassified_blocks(db_session) == 1
    db_session.refresh(ticket)
    assert ticket.block_kind is BlockKind.HUMAN_ACTION


def test_an_editor_trust_refusal_is_harness():
    assert (
        classify_block_message("⚠ Workspace Trust Required   Cursor Agent can execute code…")
        is BlockKind.HARNESS
    )


def test_the_kind_clears_when_the_stage_leaves_blocked(db_session, ticket):
    """574 sat at a legitimate test-break sign-off still badged `work`."""
    from loregarden.services.orchestration import OrchestrationService
    from loregarden.services.workflow_state import set_stage_status

    ticket.block_kind = BlockKind.WORK
    instance, stages = OrchestrationService(db_session)._resolve_stages(ticket)
    set_stage_status(ticket, instance, stages, IMPLEMENT, StageStatus.AWAITING)
    assert ticket.block_kind is None


def test_an_invented_kind_is_named_in_the_history(db_session, ticket):
    """sdf-39's rerun wrote `blocked_kind: "awaiting_human"`. Not a kind — but
    a different mistake from naming none, and the history should say which."""
    _complete_blocked(
        db_session,
        ticket,
        "<<<LOREGARDEN_STAGE_REPORT>>>\n"
        + json.dumps(
            {
                "status": "blocked",
                "confidence": 0.9,
                "reroute_context": "pick one",
                "blocked_kind": "awaiting_human",
            }
        )
        + "\n<<<END_STAGE_REPORT>>>\n",
    )

    classified = [d for d in _decisions(db_session, ticket) if d["decision"] == "classified_block"]
    assert "unknown kind 'awaiting_human'" in classified[0]["reason"]
