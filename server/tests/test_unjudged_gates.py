"""A human gate closes itself when its review kit says nothing is a person's to judge, and only then."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from loregarden.models.domain import (
    Approval,
    ApprovalKind,
    ApprovalStatus,
    ArtifactKind,
    OrchestratorDecision,
    StageStatus,
    Ticket,
)
from loregarden.services.autopilot_sign_off import GateSignerKind, signer_for_gate
from loregarden.services.orchestration import OrchestrationService
from loregarden.services.subtree_auto_run import resolve_gate_if_permitted
from loregarden.services.unjudged_gate_invariant import close_unjudged_gates, unjudged_gates
from loregarden.services.unjudged_gates import RESOLVED_BY_NOTHING_TO_JUDGE
from loregarden.services.workflow_service import resolve_ticket_stages, workflow_instance_for
from loregarden.services.workflow_state import parse_stage_map
from loregarden.testing.factories import make_artifact
from sqlmodel import Session, select
from tests.test_criterion_gate import _decisions, _orch_run, _ticket_on
from tests.test_person_judgment import NOTHING, SHAPE, STEP, _kit, _persons, _stage


@pytest.fixture(name="ticket")
def ticket_fixture(db_session: Session) -> Ticket:
    """A Blobert-shaped ticket whose AC gate passed, so the playtest is next."""
    return _ticket_on(db_session, SHAPE, through_gate=True)


def _park(session: Session, ticket: Ticket) -> Approval:
    """Open the playtest gate as a stage finished outside a run does."""
    OrchestrationService(session).enter_human_gate(ticket, stage_key="playtest")
    return session.exec(
        select(Approval).where(
            Approval.ticket_id == ticket.id,
            Approval.stage_key == "playtest",
            Approval.kind == ApprovalKind.WORKFLOW_GATE,
        )
    ).one()


def _playtest_status(session: Session, ticket: Ticket) -> StageStatus | None:
    _, stages = resolve_ticket_stages(session, ticket)
    return parse_stage_map(workflow_instance_for(session, ticket.id), stages).get("playtest")


def test_a_gate_with_nothing_to_judge_closes_and_says_why(db_session, ticket):
    _kit(db_session, ticket, {"nothing_to_judge": NOTHING})
    approval = _park(db_session, ticket)

    signer = signer_for_gate(db_session, ticket, None, approval)
    assert signer is not None and signer.kind is GateSignerKind.NOTHING_TO_JUDGE
    assert [v.subject_id for v in unjudged_gates(db_session)] == [approval.id]

    assert close_unjudged_gates(db_session, unjudged_gates(db_session)) == 1

    db_session.refresh(approval)
    assert approval.status is ApprovalStatus.APPROVED
    assert approval.resolved_by == RESOLVED_BY_NOTHING_TO_JUDGE
    assert _playtest_status(db_session, ticket) is StageStatus.DONE
    (record,) = [
        p
        for p in _decisions(db_session, ticket)
        if OrchestratorDecision.SIGNED_OFF_NOTHING_TO_JUDGE.value in p
    ]
    assert NOTHING in record
    assert unjudged_gates(db_session) == []


def test_a_run_parked_at_the_gate_closes_it_and_says_why(db_session, ticket):
    """The orchestration path: the run that parks there signs it, through `signer_for_gate`."""
    _kit(db_session, ticket, {"nothing_to_judge": NOTHING})
    approval = _park(db_session, ticket)

    assert resolve_gate_if_permitted(
        db_session,
        ticket,
        _orch_run(db_session, ticket),
        _stage(db_session, ticket, "playtest"),
        auto_approve=False,
    )

    db_session.refresh(approval)
    assert approval.status is ApprovalStatus.APPROVED
    assert approval.resolved_by == RESOLVED_BY_NOTHING_TO_JUDGE
    assert any(
        OrchestratorDecision.SIGNED_OFF_NOTHING_TO_JUDGE.value in p and NOTHING in p
        for p in _decisions(db_session, ticket)
    )


def _stays_for_a_person(session: Session, ticket: Ticket) -> None:
    approval = _park(session, ticket)
    assert signer_for_gate(session, ticket, None, approval) is None
    assert unjudged_gates(session) == []
    assert close_unjudged_gates(session, []) == 0
    session.refresh(approval)
    assert approval.status is ApprovalStatus.PENDING
    assert _playtest_status(session, ticket) is StageStatus.AWAITING


def test_a_gate_with_judgments_waits_for_a_person(db_session, ticket):
    _kit(db_session, ticket, {"steps": [STEP]})
    _stays_for_a_person(db_session, ticket)


def test_a_gate_with_no_kit_waits_for_a_person(db_session, ticket):
    _stays_for_a_person(db_session, ticket)


def test_a_newer_kit_with_judgments_outranks_an_older_nothing(db_session, ticket):
    _kit(db_session, ticket, {"nothing_to_judge": NOTHING})
    _kit(db_session, ticket, {"steps": [STEP]})
    _stays_for_a_person(db_session, ticket)


def test_an_unreadable_kit_keeps_the_gate_for_a_person(db_session, ticket):
    """It may have been the kit that asked for a judgment."""
    _kit(db_session, ticket, {"nothing_to_judge": NOTHING})
    make_artifact(
        db_session,
        ticket_id=ticket.id,
        kind=ArtifactKind.EVIDENCE,
        content={"review": {"steps": [{"action": "Press C"}]}},
        title="broken kit",
    )
    _stays_for_a_person(db_session, ticket)


def test_a_criterion_a_person_answers_keeps_the_gate(db_session, ticket):
    """A kit cannot say a person has nothing to judge over a criterion settled as theirs."""
    _persons(db_session, ticket)
    _kit(db_session, ticket, {"nothing_to_judge": NOTHING})
    _stays_for_a_person(db_session, ticket)


def test_a_refused_approval_stays_in_the_inbox_and_is_logged(db_session, ticket, caplog):
    _kit(db_session, ticket, {"nothing_to_judge": NOTHING})
    approval = _park(db_session, ticket)

    with patch(
        "loregarden.services.unjudged_gate_invariant.ApprovalService.resolve",
        side_effect=ValueError("unpublished work"),
    ):
        assert close_unjudged_gates(db_session, unjudged_gates(db_session)) == 0

    db_session.refresh(approval)
    assert approval.status is ApprovalStatus.PENDING
    assert any(
        r.levelname == "WARNING" and ticket.external_id in r.getMessage() for r in caplog.records
    )
