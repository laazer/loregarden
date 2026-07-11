"""Detect when Loregarden can safely restart during self-improvement workflows."""

from __future__ import annotations

from loregarden.models.domain import (
    AgentRun,
    OrchestrationRun,
    OrchestrationRunStatus,
    RunStatus,
    StageStatus,
    Ticket,
    Workspace,
)
from loregarden.services.orchestration import OrchestrationService
from loregarden.services.studio_service import is_agentless_stage
from sqlmodel import Session, col, select

_ACTIVE_AGENT_STATUSES = frozenset({RunStatus.RUNNING, RunStatus.AWAITING_PERMISSION})


def _human_gate_ticket_payload(
    session: Session,
    ticket: Ticket,
    orch: OrchestrationService,
) -> dict | None:
    if ticket.workflow_stage_status != StageStatus.AWAITING:
        return None

    _instance, stages = orch._resolve_stages(ticket)
    if not stages:
        return None

    stage_key = ticket.workflow_stage_key
    stage_def = next((stage for stage in stages if stage.key == stage_key), None)
    if not stage_def or not is_agentless_stage(stage_def):
        return None

    return {
        "ticket_id": ticket.id,
        "external_id": ticket.external_id,
        "title": ticket.title,
        "workflow_stage_key": stage_key,
        "workflow_stage_status": ticket.workflow_stage_status.value,
        "revision": ticket.revision,
        "restart_key": f"{ticket.id}:{stage_key}:{ticket.revision}",
    }


def evaluate_self_improve_restart(
    session: Session,
    *,
    workspace_slug: str = "loregarden",
) -> dict:
    """Return whether the dev server may restart for a human-triage handoff."""
    workspace = session.exec(select(Workspace).where(Workspace.slug == workspace_slug)).first()
    if not workspace:
        raise ValueError(f"Workspace not found: {workspace_slug}")

    orch = OrchestrationService(session)
    tickets = list(session.exec(select(Ticket).where(Ticket.workspace_id == workspace.id)).all())

    human_gate_tickets = [
        payload
        for ticket in tickets
        if (payload := _human_gate_ticket_payload(session, ticket, orch)) is not None
    ]

    active_agent_runs = list(
        session.exec(
            select(AgentRun).where(
                AgentRun.workspace_id == workspace.id,
                col(AgentRun.status).in_(_ACTIVE_AGENT_STATUSES),
            )
        ).all()
    )
    active_orchestrations = list(
        session.exec(
            select(OrchestrationRun).where(
                OrchestrationRun.workspace_id == workspace.id,
                OrchestrationRun.status == OrchestrationRunStatus.RUNNING,
            )
        ).all()
    )
    running_workflow_tickets = [
        ticket for ticket in tickets if ticket.workflow_stage_status == StageStatus.RUNNING
    ]

    blockers: list[str] = []
    if not human_gate_tickets:
        blockers.append("no_ticket_at_human_triage")
    if active_agent_runs:
        blockers.append("active_agent_runs")
    if active_orchestrations:
        blockers.append("active_orchestrations")
    if running_workflow_tickets:
        blockers.append("running_workflow_stages")

    ready = not blockers
    restart_key = human_gate_tickets[0]["restart_key"] if human_gate_tickets else ""

    return {
        "workspace_slug": workspace_slug,
        "ready": ready,
        "restart_key": restart_key,
        "blockers": blockers,
        "human_gate_tickets": human_gate_tickets,
        "active_agent_runs": [
            {
                "id": run.id,
                "run_code": run.run_code,
                "ticket_id": run.ticket_id,
                "stage_key": run.stage_key,
                "status": run.status.value,
            }
            for run in active_agent_runs
        ],
        "active_orchestrations": [
            {
                "id": run.id,
                "run_code": run.run_code,
                "ticket_id": run.ticket_id,
                "status": run.status.value,
            }
            for run in active_orchestrations
        ],
        "running_workflow_tickets": [
            {
                "ticket_id": ticket.id,
                "external_id": ticket.external_id,
                "workflow_stage_key": ticket.workflow_stage_key,
            }
            for ticket in running_workflow_tickets
        ],
    }
