"""A passing spec whose criteria are operations on shared state becomes a decision (782).

The patterns are pinned against real criteria: 782's own, which should have
been caught, and the code criteria that broader patterns flagged in the live
database, which must not be.
"""

from __future__ import annotations

import json
from unittest.mock import patch
from uuid import uuid4

import pytest
from loregarden.models.domain import (
    AgentRun,
    Approval,
    ApprovalKind,
    ApprovalStatus,
    OrchestrationRun,
    OrchestrationRunStatus,
    RunStatus,
    StageStatus,
    Ticket,
    TicketRelation,
    TicketState,
    WorkflowInstance,
    WorkflowStageDef,
    WorkflowTemplate,
    WorkItemType,
    Workspace,
)
from loregarden.services.acceptance_criteria import load_criteria, serialize_criteria
from loregarden.services.operation_criteria import (
    KEEP_OPTION,
    SPLIT_OPTION,
    operation_criteria,
)
from loregarden.services.orchestration import ApprovalService, OrchestrationService
from loregarden.services.workflow_state import initial_stages_json, parse_stage_map
from sqlmodel import Session, select
from tests.history_helpers import decision_kinds

#: 782's criteria before the split (abridged only where the text ran on).
OPERATION_782 = [
    "AC-B3: any ticket whose landed_sha pointed into the abandoned history and whose work the "
    "new branch carries has that row updated through loregarden_update_ticket to the new sha.",
    "AC-B5: the 7 commits are NOT re-signed in place; the ruleset-19407647 admin bypass is NOT "
    "used; and the force-update of the published ref plus the new PR are raised through "
    "loregarden_request_approval.",
]

#: Code criteria from the live database that broader patterns flagged.
CODE_CRITERIA = [
    "When a ticket's workflow reaches its terminal stage, its branch is merged into its target "
    "branch with a merge commit, and `landed_sha` records it",  # 768
    "The orchestrator does not choose a child whose prerequisite is `done` but has no "
    "`landed_sha`, and the hold reason says so",  # 770
    "landed_sha has one documented meaning (landing merge commit or branch tip)",  # 928
    "Opening a PR for a ticket pushes commits from that ticket's worktree, not the shared "
    "checkout",  # lg-worktree-isolation-51
    "AC-A1: MergeOutcome.signed exists, typed bool | None, defaulting to None.",  # 782's code half
    "AC31 — SCOPE FENCE. No criterion here requires changing shared state (no force-push, no "
    "ref republish, no PR opened by hand, no landed_sha edited through the tools) — it stays a "
    "pure code ticket.",  # lg-external-harness-1026, held at spec 2026-10-16
]


def test_782s_operation_criteria_are_caught():
    assert operation_criteria(OPERATION_782) == OPERATION_782


def test_code_criteria_that_mention_the_same_nouns_are_not():
    assert operation_criteria(CODE_CRITERIA) == []


@pytest.mark.parametrize(
    ("criterion", "asks"),
    [
        ("Never force-push the integration branch; land through the merge chain.", False),
        ("The tool refuses without force-pushing anything.", False),
        ("The pre-push hook rejects force-pushes to main.", False),
        # A negation in an earlier clause does not hide an ask in a later one.
        ("Do not edit the template; force-push the rebuilt branch afterwards.", True),
        # Nor does a negation too far back in the same clause.
        ("Nothing about the old branch matters here, so force-push the new one.", True),
    ],
)
def test_a_negated_mention_rules_the_operation_out(criterion, asks):
    assert bool(operation_criteria([criterion])) is asks


SPEC = "spec"
IMPLEMENT = "implement"
STAGES = [
    WorkflowStageDef(key=SPEC, name="Spec", order=1, agent_id="spec"),
    WorkflowStageDef(key=IMPLEMENT, name="Implement", order=2, agent_id="backend_implementer"),
    WorkflowStageDef(key="done", name="Done", order=3, terminal=True),
]
CODE_AC = CODE_CRITERIA[-1]


@pytest.fixture(name="ticket")
def ticket_fixture(db_session: Session, tmp_path) -> Ticket:
    workspace = Workspace(slug=f"op-{uuid4()}", name="Op", repo_path=str(tmp_path))
    db_session.add(workspace)
    db_session.commit()
    db_session.refresh(workspace)
    milestone = Ticket(
        external_id=f"op-ms-{uuid4()}",
        workspace_id=workspace.id,
        title="Milestone",
        work_item_type=WorkItemType.MILESTONE,
    )
    template = WorkflowTemplate(
        slug=f"op-tpl-{uuid4()}",
        name="Op",
        stages_json=json.dumps([s.model_dump(mode="json") for s in STAGES]),
        transitions_json=json.dumps(
            [
                {"from": SPEC, "to": IMPLEMENT, "when": "pass"},
                {"from": IMPLEMENT, "to": "done", "when": "pass"},
            ]
        ),
    )
    db_session.add(milestone)
    db_session.add(template)
    db_session.commit()
    # A real workspace names its template; the split creates a ticket through
    # TicketService, which needs one.
    workspace.workflow_template_id = template.id
    db_session.add(workspace)
    db_session.commit()
    ticket = Ticket(
        external_id=f"op-{uuid4()}",
        workspace_id=workspace.id,
        parent_ticket_id=milestone.id,
        title="Sign no-checkout landings",
        state=TicketState.IN_PROGRESS,
        work_item_type=WorkItemType.BUG,
        workflow_stage_key=SPEC,
        workflow_stage_status=StageStatus.RUNNING,
        acceptance_criteria_json=serialize_criteria([CODE_AC, *OPERATION_782]),
    )
    db_session.add(ticket)
    db_session.commit()
    db_session.refresh(ticket)
    db_session.add(
        WorkflowInstance(
            ticket_id=ticket.id,
            template_id=template.id,
            current_stage_key=SPEC,
            stages_json=initial_stages_json(STAGES),
        )
    )
    db_session.commit()
    return ticket


def _spec_passes(session: Session, ticket: Ticket) -> None:
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
        agent_id="spec",
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


def _question(session: Session, ticket: Ticket) -> Approval | None:
    return session.exec(
        select(Approval).where(
            Approval.ticket_id == ticket.id,
            Approval.kind == ApprovalKind.BLOCK_DECISION,
            Approval.status == ApprovalStatus.PENDING,
        )
    ).first()


def _answer(session: Session, approval: Approval, choice: str) -> bool:
    question = json.loads(approval.tool_input_json)["questions"][0]["question"]
    with patch("loregarden.services.run_service.schedule_orchestration") as scheduled:
        ApprovalService(session).resolve(approval.id, approved=True, answers={question: choice})
    return scheduled.called


def _stage_map(session: Session, ticket: Ticket) -> dict[str, StageStatus]:
    instance, stages = OrchestrationService(session)._resolve_stages(ticket)
    return parse_stage_map(instance, stages)


def test_a_passing_spec_with_operation_criteria_asks_instead_of_advancing(db_session, ticket):
    _spec_passes(db_session, ticket)

    approval = _question(db_session, ticket)
    assert approval is not None, "the spec must not advance silently"
    options = [o["label"] for o in json.loads(approval.tool_input_json)["questions"][0]["options"]]
    assert options == [SPLIT_OPTION, KEEP_OPTION]
    assert _stage_map(db_session, ticket)[IMPLEMENT] is StageStatus.PENDING
    assert ticket.workflow_stage_key == SPEC


def test_splitting_parks_the_operations_and_moves_on(db_session, ticket):
    _spec_passes(db_session, ticket)

    resumed = _answer(db_session, _question(db_session, ticket), SPLIT_OPTION)

    db_session.refresh(ticket)
    assert load_criteria(ticket.acceptance_criteria_json) == [CODE_AC]
    sibling = db_session.exec(
        select(Ticket).where(
            Ticket.parent_ticket_id == ticket.parent_ticket_id, Ticket.id != ticket.id
        )
    ).one()
    assert sibling.state is TicketState.PARKED
    assert load_criteria(sibling.acceptance_criteria_json) == OPERATION_782
    assert db_session.exec(select(TicketRelation)).first() is not None, "the two are linked"
    assert _stage_map(db_session, ticket)[SPEC] is StageStatus.DONE
    assert ticket.workflow_stage_key == IMPLEMENT
    assert ticket.state is not TicketState.BLOCKED
    assert resumed, "the run resumes at the next stage"
    assert "advanced_after_decision" in decision_kinds(db_session, ticket.id)


def test_keeping_them_moves_on_and_is_not_asked_again(db_session, ticket):
    _spec_passes(db_session, ticket)

    _answer(db_session, _question(db_session, ticket), KEEP_OPTION)

    db_session.refresh(ticket)
    assert load_criteria(ticket.acceptance_criteria_json) == [CODE_AC, *OPERATION_782]
    assert ticket.workflow_stage_key == IMPLEMENT

    # A later spec round (a reroute back to spec) does not ask about them again.
    ticket.workflow_stage_key = SPEC
    db_session.add(ticket)
    db_session.commit()
    _spec_passes(db_session, ticket)
    assert _question(db_session, ticket) is None


def test_a_spec_with_only_code_criteria_advances_as_before(db_session, ticket):
    ticket.acceptance_criteria_json = serialize_criteria([CODE_AC])
    db_session.add(ticket)
    db_session.commit()

    _spec_passes(db_session, ticket)

    assert _question(db_session, ticket) is None
    assert _stage_map(db_session, ticket)[SPEC] is StageStatus.DONE
