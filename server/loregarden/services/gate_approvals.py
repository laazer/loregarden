"""Workflow-gate approvals: raising them, and what approving one may complete.

Approving a `WORKFLOW_GATE` marks its stage DONE. That is the right answer for
a human gate (no agent; a person *is* the stage) and for a sign-off raised after
an agent stage ran (`gate_required`). It is the wrong answer for an agent stage
nothing has run: the approval then records review work that never happened, and
the workflow carries on as if it had.

The built-in driver already refuses to open a human gate on a stage that has an
agent (`OrchestrationService.open_human_gate`). The MCP `request_approval` path
did not, and on blobert ticket 26 an external driver raised a gate on the
three-reviewer `script_review` stage, a person approved it, and the ticket
reached `ac_gate` with zero reviewer runs. Same rule, every path.
"""

from __future__ import annotations

import json
import logging

from loregarden.core.event_bus import event_bus
from loregarden.models.domain import (
    AgentRun,
    Approval,
    ApprovalKind,
    ApprovalStatus,
    EventType,
    RunStatus,
    Ticket,
    WorkflowStageDef,
)
from loregarden.services.gate_checklist import expand_gate_checklist_for_ticket
from loregarden.services.studio_routing import is_agentless_stage
from sqlmodel import Session, select

logger = logging.getLogger(__name__)


def build_gate_impact(ticket: Ticket, stage_name: str) -> str:
    lines = [f"Stage '{stage_name}' requires human sign-off before completion."]
    lines.append(f"What's being tested: {ticket.title}")
    if ticket.description.strip():
        lines.append(ticket.description.strip())
    try:
        criteria = json.loads(ticket.acceptance_criteria_json or "[]")
    except json.JSONDecodeError:
        logger.warning(
            "Unparseable acceptance_criteria_json on ticket %s; the %s gate brief omits it",
            ticket.external_id,
            stage_name,
            exc_info=True,
        )
        criteria = []
    if criteria:
        lines.append("Acceptance criteria:")
        lines.extend(f"- {item}" for item in criteria)
    return "\n".join(lines)


def create_workflow_gate_approval(
    session: Session,
    ticket: Ticket,
    stage_key: str,
    stage_name: str,
    *,
    stage_def: WorkflowStageDef | None = None,
) -> Approval:
    checklist = expand_gate_checklist_for_ticket(
        session, ticket, list(stage_def.checklist) if stage_def else []
    )
    approval = Approval(
        ticket_id=ticket.id,
        workspace_id=ticket.workspace_id,
        kind=ApprovalKind.WORKFLOW_GATE,
        title=f"Approve {ticket.title}",
        level="high" if ticket.priority == 1 else "medium",
        stage_key=stage_key,
        impact=build_gate_impact(ticket, stage_name),
        checklist_json=json.dumps(checklist),
        status=ApprovalStatus.PENDING,
    )
    session.add(approval)
    session.commit()
    event_bus.publish(
        session,
        EventType.APPROVAL_REQUESTED,
        workspace_id=ticket.workspace_id,
        ticket_id=ticket.id,
        payload={"approval_id": approval.id},
    )
    return approval


def gate_would_skip_work(session: Session, ticket: Ticket, stage: WorkflowStageDef) -> str:
    """Why approving a gate on `stage` would complete work nothing did, or "".

    A stage is safe to sign off when it has no agent (a person is the stage) or
    when an agent run for it has already succeeded on this ticket (the sign-off
    comes after the work, as `gate_required` raises it). Otherwise the approval
    would stand in for the run, and the message names the two honest moves.
    """
    if is_agentless_stage(stage):
        return ""
    ran = session.exec(
        select(AgentRun.id).where(
            AgentRun.ticket_id == ticket.id,
            AgentRun.stage_key == stage.key,
            AgentRun.status == RunStatus.SUCCEEDED,
        )
    ).first()
    if ran:
        return ""
    return (
        f"Stage '{stage.key}' has agents and none has succeeded on this ticket; "
        "approving a gate here would mark the stage done with no work behind it. "
        "Run it (start_stage; begin_external_stage from an external driver, which "
        "fans out a parallel stage's members) or waive it with a reason (skip_stage)."
    )
