"""Serialize approval records for inbox and triage APIs."""

from __future__ import annotations

import json

from loregarden.core.workflow_loader import stage_display_name
from loregarden.models.domain import (
    Approval,
    ApprovalKind,
    ApprovalStatus,
    Ticket,
    WorkflowTemplate,
    Workspace,
)
from loregarden.services.gate_checklist import expand_gate_checklist_for_ticket
from loregarden.services.prepared_action import PreparedAction
from pydantic import BaseModel, ConfigDict, ValidationError
from sqlmodel import Session


def _gate_route_options(session: Session, ticket: Ticket | None, gate_stage_key: str) -> list[dict]:
    """Stages upstream of a workflow gate, offered as approve-and-rework targets."""
    from loregarden.services.workflow_service import resolve_ticket_stages

    if not ticket or not gate_stage_key:
        return []
    _, stages = resolve_ticket_stages(session, ticket)
    ordered = sorted(stages, key=lambda s: s.order)
    options: list[dict] = []
    for stage in ordered:
        if stage.key == gate_stage_key:
            break
        options.append({"key": stage.key, "name": stage.name})
    return options


class _QuestionPayload(BaseModel):
    """The shape a CLI_QUESTION approval stores in `tool_input_json`.

    Modelled rather than shape-checked by hand: the payload is written by a CLI
    adapter, and `questions` arriving as something other than a list used to be
    caught with `isinstance`, which is the hand-rolled schema check the
    organization gate exists to stop. `extra="ignore"` does that work, and a
    payload of the wrong shape now fails validation in one place.
    """

    model_config = ConfigDict(extra="ignore")

    questions: list[dict] = []


def _parsed_questions(approval: Approval) -> list[dict]:
    """The questions a CLI_QUESTION approval carries, or none for other kinds."""
    if approval.kind != ApprovalKind.CLI_QUESTION:
        return []
    try:
        return _QuestionPayload.model_validate_json(approval.tool_input_json or "{}").questions
    except ValidationError:
        return []


def _parsed_prepared_action(approval: Approval) -> dict | None:
    """The prepared action a HUMAN_ACTION approval carries.

    Parsed here rather than in the client, the same way `questions` is: the
    shape is the server's contract, and a UI re-deriving it drifts
    (lg-workflow-integrity-460).
    """
    if approval.kind != ApprovalKind.HUMAN_ACTION:
        return None
    try:
        action = PreparedAction.model_validate_json(approval.tool_input_json or "{}")
    except ValidationError:
        return None
    return action.model_dump(mode="json")


def approval_to_view(session: Session, approval: Approval) -> dict:
    # Workspace-scoped approvals (Home Baxter chat) carry no ticket.
    ticket = session.get(Ticket, approval.ticket_id) if approval.ticket_id else None
    ws = session.get(Workspace, approval.workspace_id)
    stage_name = approval.stage_key
    if ws and ws.workflow_template_id:
        tpl = session.get(WorkflowTemplate, ws.workflow_template_id)
        if tpl:
            stage_name = stage_display_name(tpl, approval.stage_key)

    questions = _parsed_questions(approval)

    resolved_answers = None
    if approval.response_json and approval.response_json != "{}":
        try:
            stored = json.loads(approval.response_json)
            resolved_answers = stored.get("updated_input", {}).get("answers")
        except json.JSONDecodeError:
            resolved_answers = None

    try:
        checklist = json.loads(approval.checklist_json or "[]")
    except json.JSONDecodeError:
        checklist = []
    if ticket:
        # Gates recorded before the checklist was expanded still hold a raw
        # {{acceptance_criteria}} token; expand on read so it never reaches the UI.
        checklist = expand_gate_checklist_for_ticket(session, ticket, checklist)

    prepared_action = _parsed_prepared_action(approval)

    route_options: list[dict] = []
    if approval.kind == ApprovalKind.WORKFLOW_GATE and approval.status == ApprovalStatus.PENDING:
        route_options = _gate_route_options(session, ticket, approval.stage_key)

    return {
        "id": approval.id,
        "title": approval.title,
        "level": approval.level,
        "workspace_slug": ws.slug if ws else "",
        "stage_key": approval.stage_key,
        "stage_name": stage_name,
        "impact": approval.impact,
        "checklist": checklist,
        "route_options": route_options,
        "prepared_action": prepared_action,
        "ticket_id": approval.ticket_id or "",
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
