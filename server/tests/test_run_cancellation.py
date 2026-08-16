"""Cooperative cancellation of in-flight agent and orchestration runs."""

from datetime import datetime, timezone
from unittest.mock import patch

import pytest
from loregarden.models.domain import (
    AgentRun,
    Approval,
    ApprovalKind,
    ApprovalStatus,
    Artifact,
    OrchestrationRun,
    OrchestrationRunStatus,
    QueuedRun,
    QueuePosition,
    RunStatus,
    StageStatus,
    Ticket,
)
from loregarden.services.orchestration import OrchestrationService
from loregarden.services.run_cancellation import (
    cancel_refusal,
    cancel_requested,
    request_cancel,
    request_orchestration_cancel,
)
from loregarden.services.workflow_state import parse_stage_map
from sqlmodel import Session, select


def _seed_ticket(session: Session) -> Ticket:
    ticket = session.exec(
        select(Ticket).where(Ticket.legacy_external_id == "03-wire-cli-agent-runner")
    ).first()
    assert ticket is not None
    return ticket


def _run(
    session: Session,
    ticket: Ticket,
    *,
    status: RunStatus = RunStatus.RUNNING,
    stage_key: str = "testing",
) -> AgentRun:
    run = AgentRun(
        run_code="run_cancel",
        ticket_id=ticket.id,
        workspace_id=ticket.workspace_id,
        agent_id="planner",
        stage_key=stage_key,
        status=status,
        started_at=datetime.now(timezone.utc),
    )
    session.add(run)
    session.commit()
    session.refresh(run)
    return run


def test_request_cancel_sets_the_flag(db_session: Session):
    ticket = _seed_ticket(db_session)
    run = _run(db_session, ticket)

    assert cancel_refusal(run) == ""
    updated = request_cancel(db_session, run)
    assert updated.cancel_requested_at is not None
    assert cancel_requested(run.id) is True


def test_a_finished_run_cannot_be_cancelled(db_session: Session):
    ticket = _seed_ticket(db_session)
    run = _run(db_session, ticket, status=RunStatus.SUCCEEDED)

    assert "nothing to cancel" in cancel_refusal(run)
    with pytest.raises(ValueError, match="nothing to cancel"):
        request_cancel(db_session, run)


def test_cancelled_completion_leaves_stage_pending(db_session: Session):
    ticket = _seed_ticket(db_session)
    orch = OrchestrationService(db_session)
    run = orch.start_run(ticket, stage_key="testing")

    orch.complete_run(
        run,
        status=RunStatus.CANCELLED,
        stderr="Cancelled by operator",
    )

    db_session.refresh(ticket)
    assert ticket.workflow_stage_status == StageStatus.PENDING
    assert ticket.blocking_issues == ""

    instance, stages = orch._resolve_stages(ticket)
    assert instance is not None and stages is not None
    assert parse_stage_map(instance, stages).get("testing") == StageStatus.PENDING

    errors = db_session.exec(
        select(Artifact).where(Artifact.run_id == run.id, Artifact.kind == "error")
    ).all()
    assert errors == []


def test_queued_cancel_drops_parallel_queue_row(db_session: Session):
    ticket = _seed_ticket(db_session)
    run = _run(db_session, ticket, status=RunStatus.QUEUED)
    db_session.add(
        QueuedRun(
            workspace_id=ticket.workspace_id,
            ticket_id=ticket.id,
            run_id=run.id,
            position=1,
            status=QueuePosition.QUEUED,
        )
    )
    db_session.commit()

    updated = request_cancel(db_session, run)
    assert updated.status == RunStatus.CANCELLED
    assert db_session.exec(select(QueuedRun).where(QueuedRun.run_id == run.id)).first() is None


def test_awaiting_permission_cancel_rejects_pending_approvals(db_session: Session):
    ticket = _seed_ticket(db_session)
    run = _run(db_session, ticket, status=RunStatus.AWAITING_PERMISSION)
    approval = Approval(
        ticket_id=ticket.id,
        workspace_id=ticket.workspace_id,
        run_id=run.id,
        kind=ApprovalKind.CLI_PERMISSION,
        title="Allow Bash",
        status=ApprovalStatus.PENDING,
    )
    db_session.add(approval)
    db_session.commit()

    request_cancel(db_session, run)
    db_session.refresh(approval)
    assert approval.status == ApprovalStatus.REJECTED
    assert approval.resolved_by == "cancellation"
    assert cancel_requested(run.id) is True


def test_orchestration_cancel_sets_the_flag(db_session: Session):
    ticket = _seed_ticket(db_session)
    orch = OrchestrationRun(
        run_code="orch_cancel",
        ticket_id=ticket.id,
        workspace_id=ticket.workspace_id,
        status=OrchestrationRunStatus.RUNNING,
        started_at=datetime.now(timezone.utc),
    )
    db_session.add(orch)
    db_session.commit()
    db_session.refresh(orch)

    updated = request_orchestration_cancel(db_session, orch)
    assert updated.cancel_requested_at is not None


def test_the_api_cancels_a_running_run(client, db_session: Session):
    ticket = _seed_ticket(db_session)
    run = _run(db_session, ticket)

    response = client.post(f"/api/runs/{run.id}/cancel")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["cancel_requested_at"] is not None

    db_session.refresh(run)
    assert run.cancel_requested_at is not None


def test_cancel_for_an_unknown_run_is_404(client):
    assert client.post("/api/runs/nope/cancel").status_code == 404


def test_cancel_for_a_finished_run_is_409(client, db_session: Session):
    ticket = _seed_ticket(db_session)
    run = _run(db_session, ticket, status=RunStatus.FAILED)

    response = client.post(f"/api/runs/{run.id}/cancel")
    assert response.status_code == 409
    assert "nothing to cancel" in response.json()["detail"]


def test_ticket_stop_cancels_in_flight_runs(client, db_session: Session):
    ticket = _seed_ticket(db_session)
    run = _run(db_session, ticket)
    orch = OrchestrationRun(
        run_code="orch_stop",
        ticket_id=ticket.id,
        workspace_id=ticket.workspace_id,
        status=OrchestrationRunStatus.RUNNING,
    )
    db_session.add(orch)
    db_session.commit()

    response = client.post(f"/api/tickets/{ticket.id}/stop")
    assert response.status_code == 200, response.text

    db_session.refresh(run)
    db_session.refresh(orch)
    assert run.cancel_requested_at is not None
    assert orch.cancel_requested_at is not None


def test_ticket_stop_for_unknown_ticket_is_404(client):
    assert client.post("/api/tickets/nope/stop").status_code == 404


def test_cancel_requested_uses_its_own_session(db_session: Session):
    """The poller must not rely on the run-driving session — same trap as steer."""
    ticket = _seed_ticket(db_session)
    run = _run(db_session, ticket)
    request_cancel(db_session, run)

    with patch("loregarden.services.run_cancellation.Session") as mock_session_cls:
        mock_session_cls.return_value.__enter__.return_value.get.return_value = run
        assert cancel_requested(run.id) is True
        mock_session_cls.assert_called()
