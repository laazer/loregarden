"""Tests for self-improve restart readiness detection."""

from fastapi.testclient import TestClient
from loregarden.models.domain import (
    AgentRun,
    OrchestrationDriver,
    OrchestrationRun,
    OrchestrationRunStatus,
    RunStatus,
    StageStatus,
    Ticket,
    TicketState,
    WorkflowInstance,
    Workspace,
)
from loregarden.services.self_improve_restart import evaluate_self_improve_restart
from loregarden.services.workflow_service import resolve_workspace_stages
from loregarden.services.workflow_state import set_stage_status
from sqlmodel import Session, select


def _ticket_id_by_external_id(client: TestClient, external_id: str) -> str:
    for ticket in client.get("/api/tickets").json():
        if ticket["external_id"] == external_id:
            return ticket["id"]
    raise AssertionError(f"ticket not found: {external_id}")


def _ticket_at_human_gate(db_session: Session, ticket_id: str) -> Ticket:
    ticket = db_session.get(Ticket, ticket_id)
    assert ticket is not None
    instance = db_session.exec(
        select(WorkflowInstance).where(WorkflowInstance.ticket_id == ticket.id)
    ).first()
    assert instance is not None
    ws = db_session.get(Workspace, ticket.workspace_id)
    assert ws is not None
    _, stages = resolve_workspace_stages(db_session, ws)
    ticket.state = TicketState.IN_PROGRESS
    ticket.workflow_stage_key = "approval"
    ticket.revision = 3
    set_stage_status(ticket, instance, stages, "approval", StageStatus.AWAITING)
    db_session.add(ticket)
    db_session.add(instance)
    db_session.commit()
    db_session.refresh(ticket)
    return ticket


def test_ready_when_ticket_at_human_gate_and_no_active_work(
    client: TestClient, db_session: Session
):
    ticket = _ticket_at_human_gate(
        db_session, _ticket_id_by_external_id(client, "04-workflow-template-overrides")
    )

    result = evaluate_self_improve_restart(db_session, workspace_slug="loregarden")

    assert result["ready"] is True
    assert result["restart_key"] == f"{ticket.id}:approval:3"
    assert result["human_gate_tickets"][0]["external_id"] == ticket.external_id
    assert result["blockers"] == []


def test_not_ready_while_agent_stream_active(client: TestClient, db_session: Session):
    ticket = _ticket_at_human_gate(
        db_session, _ticket_id_by_external_id(client, "04-workflow-template-overrides")
    )
    workspace = db_session.get(Workspace, ticket.workspace_id)
    assert workspace is not None
    db_session.add(
        AgentRun(
            run_code="run_active",
            ticket_id=ticket.id,
            workspace_id=workspace.id,
            agent_id="backend_implementer",
            stage_key="implementation",
            status=RunStatus.RUNNING,
        )
    )
    db_session.commit()

    result = evaluate_self_improve_restart(db_session, workspace_slug="loregarden")

    assert result["ready"] is False
    assert "active_agent_runs" in result["blockers"]


def test_not_ready_while_orchestration_active(client: TestClient, db_session: Session):
    ticket = _ticket_at_human_gate(
        db_session, _ticket_id_by_external_id(client, "04-workflow-template-overrides")
    )
    workspace = db_session.get(Workspace, ticket.workspace_id)
    assert workspace is not None
    db_session.add(
        OrchestrationRun(
            run_code="orch_active",
            ticket_id=ticket.id,
            workspace_id=workspace.id,
            driver=OrchestrationDriver.BUILTIN_AUTOPILOT,
            status=OrchestrationRunStatus.RUNNING,
        )
    )
    db_session.commit()

    result = evaluate_self_improve_restart(db_session, workspace_slug="loregarden")

    assert result["ready"] is False
    assert "active_orchestrations" in result["blockers"]


def test_not_ready_while_other_workflow_stage_running(client: TestClient, db_session: Session):
    workspace = db_session.exec(select(Workspace).where(Workspace.slug == "loregarden")).first()
    assert workspace is not None
    _ticket_at_human_gate(
        db_session, _ticket_id_by_external_id(client, "04-workflow-template-overrides")
    )
    other = Ticket(
        external_id="other-running-workflow",
        title="Other running workflow",
        workspace_id=workspace.id,
        state=TicketState.IN_PROGRESS,
        workflow_stage_key="implementation",
        workflow_stage_status=StageStatus.RUNNING,
    )
    db_session.add(other)
    db_session.commit()

    result = evaluate_self_improve_restart(db_session, workspace_slug="loregarden")

    assert result["ready"] is False
    assert "running_workflow_stages" in result["blockers"]


def test_self_improve_restart_api(client: TestClient):
    res = client.get("/api/system/self-improve-restart?workspace=loregarden")
    assert res.status_code == 200
    body = res.json()
    assert "ready" in body
    assert "blockers" in body
    assert "human_gate_tickets" in body
