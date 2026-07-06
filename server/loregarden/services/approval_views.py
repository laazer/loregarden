"""Serialize approval records for inbox and triage APIs."""

from __future__ import annotations

import json

from loregarden.core.workflow_loader import stage_display_name
from loregarden.models.domain import Approval, ApprovalKind, Ticket, WorkflowTemplate, Workspace
from sqlmodel import Session


def approval_to_view(session: Session, approval: Approval) -> dict:
    ticket = session.get(Ticket, approval.ticket_id)
    ws = session.get(Workspace, approval.workspace_id)
    stage_name = approval.stage_key
    if ws and ws.workflow_template_id:
        tpl = session.get(WorkflowTemplate, ws.workflow_template_id)
        if tpl:
            stage_name = stage_display_name(tpl, approval.stage_key)

    questions = []
    if approval.kind == ApprovalKind.CLI_QUESTION:
        try:
            payload = json.loads(approval.tool_input_json or "{}")
            if isinstance(payload.get("questions"), list):
                questions = payload["questions"]
        except json.JSONDecodeError:
            questions = []

    resolved_answers = None
    if approval.response_json and approval.response_json != "{}":
        try:
            stored = json.loads(approval.response_json)
            resolved_answers = stored.get("updated_input", {}).get("answers")
        except json.JSONDecodeError:
            resolved_answers = None

    return {
        "id": approval.id,
        "title": approval.title,
        "level": approval.level,
        "workspace_slug": ws.slug if ws else "",
        "stage_key": approval.stage_key,
        "stage_name": stage_name,
        "impact": approval.impact,
        "ticket_id": approval.ticket_id,
        "ticket_external_id": ticket.external_id if ticket else "",
        "kind": approval.kind.value if hasattr(approval.kind, "value") else str(approval.kind),
        "status": approval.status.value
        if hasattr(approval.status, "value")
        else str(approval.status),
        "run_id": approval.run_id or "",
        "tool_name": approval.tool_name,
        "tool_input_json": approval.tool_input_json,
        "cli_adapter": approval.cli_adapter,
        "questions": questions,
        "resolved_answers": resolved_answers,
        "created_at": approval.created_at.isoformat() if approval.created_at else "",
        "resolved_at": approval.resolved_at.isoformat() if approval.resolved_at else "",
    }
