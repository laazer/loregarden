"""Resolving a human gate carries the workflow on, rather than parking it."""

from unittest.mock import patch

import pytest
from loregarden.models.domain import (
    Approval,
    ApprovalKind,
    ApprovalStatus,
    OrchestrationRun,
    OrchestrationRunStatus,
    StageStatus,
    Ticket,
)
from loregarden.services.orchestration import ApprovalService
from sqlmodel import Session, select


@pytest.fixture
def gated_ticket(db_session: Session):
    """A ticket parked on its gate stage, as a human-approval pause leaves it."""
    from loregarden.services.orchestration import OrchestrationService

    ticket = db_session.exec(select(Ticket)).first()
    orch = OrchestrationService(db_session)
    orch.ensure_workflow_instance(ticket)
    instance, stages = orch._resolve_stages(ticket)
    assert instance and stages

    gate = next((s for s in stages if s.stage_type == "gate"), stages[-1])
    ticket.workflow_stage_key = gate.key
    ticket.workflow_stage_status = StageStatus.AWAITING
    db_session.add(ticket)
    db_session.commit()

    approval = Approval(
        ticket_id=ticket.id,
        workspace_id=ticket.workspace_id,
        kind=ApprovalKind.WORKFLOW_GATE,
        stage_key=gate.key,
        status=ApprovalStatus.PENDING,
        title="sign off",
    )
    db_session.add(approval)
    db_session.commit()
    return ticket, approval, instance, stages


def test_a_plain_approval_carries_the_workflow_on(db_session, gated_ticket):
    """The common case, and the one that used to park the ticket.

    Only approve-with-rework resumed. A plain approval marked the stage done
    and stopped, so the operator had to press Run to act on a decision they had
    just made.
    """
    ticket, approval, _, _ = gated_ticket
    with patch("loregarden.services.run_service.schedule_orchestration") as scheduled:
        ApprovalService(db_session).resolve(approval.id, approved=True)

    scheduled.assert_called_once()
    assert scheduled.call_args.args[0] == ticket.id


def test_a_rejection_does_not_carry_on(db_session, gated_ticket):
    """Deliberately asymmetric with approval.

    A rejection means more work is needed, and the operator may want to add
    guidance — or steer the stage once it starts — before anything spends
    tokens on the rework. Approving says "go"; rejecting does not.
    """
    _, approval, _, _ = gated_ticket
    with patch("loregarden.services.run_service.schedule_orchestration") as scheduled:
        ApprovalService(db_session).resolve(
            approval.id, approved=False, response_text="not good enough"
        )

    scheduled.assert_not_called()


def test_a_live_orchestration_is_not_double_scheduled(db_session, gated_ticket):
    """The running orchestration will pick the stage up itself."""
    ticket, approval, _, _ = gated_ticket
    db_session.add(
        OrchestrationRun(
            run_code="orch_live",
            ticket_id=ticket.id,
            workspace_id=ticket.workspace_id,
            status=OrchestrationRunStatus.RUNNING,
        )
    )
    db_session.commit()

    with patch("loregarden.services.run_service.schedule_orchestration") as scheduled:
        ApprovalService(db_session).resolve(approval.id, approved=True)

    scheduled.assert_not_called()


def test_the_resumed_run_keeps_auto_approve(db_session, gated_ticket):
    """Resuming without it would quietly turn an unattended run into one that
    stops at the next tool prompt — the opposite of carrying on."""
    ticket, approval, _, _ = gated_ticket
    db_session.add(
        OrchestrationRun(
            run_code="orch_prior",
            ticket_id=ticket.id,
            workspace_id=ticket.workspace_id,
            auto_approve=True,
            status=OrchestrationRunStatus.SUCCEEDED,
        )
    )
    db_session.commit()

    with patch("loregarden.services.run_service.schedule_orchestration") as scheduled:
        ApprovalService(db_session).resolve(approval.id, approved=True)

    assert scheduled.call_args.kwargs["auto_approve"] is True


def test_an_attended_run_is_not_silently_upgraded(db_session, gated_ticket):
    ticket, approval, _, _ = gated_ticket
    db_session.add(
        OrchestrationRun(
            run_code="orch_manual",
            ticket_id=ticket.id,
            workspace_id=ticket.workspace_id,
            auto_approve=False,
            status=OrchestrationRunStatus.SUCCEEDED,
        )
    )
    db_session.commit()

    with patch("loregarden.services.run_service.schedule_orchestration") as scheduled:
        ApprovalService(db_session).resolve(approval.id, approved=True)

    assert scheduled.call_args.kwargs["auto_approve"] is False
