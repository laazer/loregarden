"""A ticket's gate signs itself off on its criteria check, and only then.

The check runs for real (`test_criterion_gate`'s throwaway checkout and pytest
runner), so the commit it records is a real HEAD, and moving HEAD is a real
commit.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest
from loregarden.models.domain import (
    Approval,
    ApprovalStatus,
    ExitActionRequirementKind,
    OperatorJudgmentRequirement,
    OrchestratorDecision,
    RunStatus,
    Ticket,
    WorkflowExitAction,
    WorkflowStageDef,
    WorkItemType,
)
from loregarden.services.auto_decisions import auto_decisions_under
from loregarden.services.autopilot_sign_off import (
    LEGACY_SIGN_OFF_KEY,
    GateSignerKind,
    signer_for_gate,
)
from loregarden.services.criteria_sign_off import RESOLVED_BY_CRITERIA
from loregarden.services.criterion_gate import check_criteria_if_due
from loregarden.services.criterion_gate_stages import gate_of
from loregarden.services.gate_approvals import create_workflow_gate_approval
from loregarden.services.orchestration_profile import GatesConfig, OrchestrationProfile
from loregarden.services.orchestrator_decisions import record_orchestrator_decision
from loregarden.services.subtree_auto_run import resolve_gate_if_permitted
from loregarden.services.workflow_service import resolve_ticket_stages
from sqlmodel import Session
from tests.factories import make_agent_run, make_workspace_ticket
from tests.test_criterion_gate import (  # noqa: F401 — fixtures used by name
    CRITERIA,
    _decisions,
    _met,
    _orch_run,
    checkout_fixture,
    env_fixture,
    ticket_fixture,
)

PASSING = "server/tests/test_arms.py::test_reads_width"

LEGACY_SIGN_OFF = WorkflowExitAction(
    key=LEGACY_SIGN_OFF_KEY,
    label="Approve Gate completion",
    requirement=OperatorJudgmentRequirement(
        kind=ExitActionRequirementKind.OPERATOR_JUDGMENT, decision_prompt="Approve?"
    ),
)


@pytest.fixture(name="head")
def head_fixture(env, checkout: Path):  # noqa: ARG001 — env points the check at the checkout
    """Point the ticket's HEAD (`evidence.resolve_head_sha`) at the same checkout."""
    with patch("loregarden.services.evidence.resolve_ticket_root", return_value=checkout):
        yield checkout


def _gate_stage(session: Session, ticket: Ticket) -> WorkflowStageDef:
    _, stages = resolve_ticket_stages(session, ticket)
    return gate_of(stages).model_copy(
        update={"exit_actions_enabled": True, "exit_actions": [LEGACY_SIGN_OFF]}
    )


def _checked(session: Session, ticket: Ticket) -> None:
    """Every criterion settled by a passing test, and the check before the gate passed."""
    for criterion in CRITERIA:
        _met(session, ticket, criterion, PASSING)
    assert not check_criteria_if_due(session, _orch_run(session, ticket), ticket)
    assert any(
        OrchestratorDecision.CRITERIA_CHECKED.value in p for p in _decisions(session, ticket)
    )


def _gatekeeper_passed_and_asked(session: Session, ticket: Ticket) -> Approval:
    """The gatekeeper's run succeeded and its legacy sign-off raised the gate, as run completion does."""
    stage = _gate_stage(session, ticket)
    make_agent_run(
        session,
        workspace_id=ticket.workspace_id,
        ticket_id=ticket.id,
        run_code=f"gk-{ticket.external_id}",
        agent_id="gatekeeper",
        stage_key=stage.key,
        status=RunStatus.SUCCEEDED,
    )
    approval = create_workflow_gate_approval(
        session, ticket, stage.key, stage.name, stage_def=stage
    )
    assert approval is not None
    return approval


def _commit(checkout: Path) -> None:
    (checkout / "server" / "later.py").write_text("x = 1\n")
    env = {
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@t",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@t",
        "PATH": "/usr/bin:/bin",
    }
    for args in (["add", "."], ["commit", "-q", "-m", "later"]):
        subprocess.run(["git", *args], cwd=checkout, check=True, env=env)


def test_the_gate_signs_itself_off_on_a_passed_criteria_check(
    db_session: Session, ticket: Ticket, head: Path
) -> None:
    _checked(db_session, ticket)
    approval = _gatekeeper_passed_and_asked(db_session, ticket)

    signer = signer_for_gate(db_session, ticket, None, approval)
    assert signer is not None and signer.kind is GateSignerKind.CRITERIA_CHECKED

    stage = _gate_stage(db_session, ticket)
    assert resolve_gate_if_permitted(
        db_session, ticket, _orch_run(db_session, ticket), stage, auto_approve=False
    )
    db_session.refresh(approval)
    assert approval.status == ApprovalStatus.APPROVED
    assert approval.resolved_by == RESOLVED_BY_CRITERIA
    signed = [
        p
        for p in _decisions(db_session, ticket)
        if OrchestratorDecision.SIGNED_OFF_ON_CRITERIA.value in p
    ]
    assert len(signed) == 1 and approval.id in signed[0]


def test_a_signed_gate_is_in_the_trees_digest(
    db_session: Session, ticket: Ticket, head: Path
) -> None:
    _checked(db_session, ticket)
    _gatekeeper_passed_and_asked(db_session, ticket)
    resolve_gate_if_permitted(
        db_session,
        ticket,
        _orch_run(db_session, ticket),
        _gate_stage(db_session, ticket),
        auto_approve=False,
    )
    milestone = db_session.get(Ticket, ticket.parent_ticket_id)
    digest = auto_decisions_under(db_session, milestone)
    assert [(v.external_id, v.decision) for v in digest] == [
        (ticket.external_id, OrchestratorDecision.SIGNED_OFF_ON_CRITERIA)
    ]


def test_the_gate_waits_for_a_person_without_a_criteria_check(
    db_session: Session, ticket: Ticket, head: Path
) -> None:
    approval = _gatekeeper_passed_and_asked(db_session, ticket)

    assert signer_for_gate(db_session, ticket, None, approval) is None
    assert not resolve_gate_if_permitted(
        db_session,
        ticket,
        _orch_run(db_session, ticket),
        _gate_stage(db_session, ticket),
        auto_approve=False,
    )
    db_session.refresh(approval)
    assert approval.status == ApprovalStatus.PENDING


def test_a_commit_after_the_check_hands_the_gate_back_to_a_person(
    db_session: Session, ticket: Ticket, head: Path
) -> None:
    _checked(db_session, ticket)
    _commit(head)
    approval = _gatekeeper_passed_and_asked(db_session, ticket)

    assert signer_for_gate(db_session, ticket, None, approval) is None


@pytest.mark.parametrize(
    "decision",
    [OrchestratorDecision.SPLIT_CRITERIA, OrchestratorDecision.DEFERRED_UNDECIDED_FINDINGS],
)
def test_a_decision_taken_in_a_persons_place_keeps_the_gate_and_is_named_on_it(
    db_session: Session, ticket: Ticket, head: Path, decision: OrchestratorDecision
) -> None:
    child = make_workspace_ticket(db_session, "cg-child")
    child.work_item_type = WorkItemType.TASK
    child.parent_ticket_id = ticket.id
    db_session.add(child)
    db_session.commit()
    record_orchestrator_decision(
        db_session, child, decision=decision, stage_key="verify", reason="moved AC9 to a sibling"
    )
    db_session.commit()
    _checked(db_session, ticket)
    approval = _gatekeeper_passed_and_asked(db_session, ticket)

    assert signer_for_gate(db_session, ticket, None, approval) is None
    assert f"{child.external_id}: moved AC9 to a sibling" in approval.impact


def test_a_workspace_can_keep_its_gates_a_persons(
    db_session: Session, ticket: Ticket, head: Path
) -> None:
    _checked(db_session, ticket)
    approval = _gatekeeper_passed_and_asked(db_session, ticket)
    off = OrchestrationProfile(
        slug="t", name="t", gates=GatesConfig(sign_off_when_criteria_checked=False)
    )
    with patch(
        "loregarden.services.criteria_sign_off.resolve_orchestration_profile", return_value=off
    ):
        assert signer_for_gate(db_session, ticket, None, approval) is None


def test_only_the_gate_the_criteria_were_checked_before_signs_itself(
    db_session: Session, ticket: Ticket, head: Path
) -> None:
    _checked(db_session, ticket)
    _, stages = resolve_ticket_stages(db_session, ticket)
    review = next(s for s in stages if s.key == "review").model_copy(
        update={"exit_actions_enabled": True, "exit_actions": [LEGACY_SIGN_OFF]}
    )
    approval = create_workflow_gate_approval(
        db_session, ticket, review.key, review.name, stage_def=review
    )

    assert signer_for_gate(db_session, ticket, None, approval) is None
