"""Workflow-gate approvals: raising them, and what approving one may complete.

Approving a `WORKFLOW_GATE` marks its stage DONE. That is the right answer for
a human gate (exit actions that need a person) and for a sign-off raised after
an agent stage ran with unresolved exit actions. It is the wrong answer for an
agent stage nothing has run: the approval then records review work that never
happened, and the workflow carries on as if it had.

The built-in driver already refuses to open a human gate on a stage that has an
agent (`OrchestrationService.enter_human_gate`). The MCP `request_approval` path
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
    ExitActionGateLedger,
    RunStatus,
    StageStatus,
    Ticket,
    WorkflowStageDef,
)
from loregarden.services.exit_action_ledger import outstanding_exit_actions
from loregarden.services.exit_actions import (
    allowed_resolution_actions,
    resolve_exit_actions,
)
from loregarden.services.gate_checklist import expand_gate_checklist_for_ticket
from loregarden.services.studio_routing import is_agentless_stage, ticket_stage_definition
from loregarden.services.workflow_service import resolve_ticket_stages, workflow_instance_for
from loregarden.services.workflow_state import set_stage_status
from sqlmodel import Session, col, select

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
    snapshot: dict | None = None,
    human_required_actions: list | None = None,
    settled_action_keys: list[str] | None = None,
    run_id: str | None = None,
    title: str = "",
    impact: str = "",
) -> Approval | None:
    """Create one pending gate for unresolved exit actions, or None when none remain.

    ``settled_action_keys`` is what the run that raised the gate (and its
    continuation chain) already settled; it rides on the gate so the
    continuation this gate may schedule does not re-run or re-ask it.
    """
    if human_required_actions is None:
        resolution = resolve_exit_actions(stage_def, snapshot)
        human_required_actions = resolution.human_required_actions
    if not human_required_actions:
        return None
    # At most one pending workflow-gate approval per stage.
    existing = session.exec(
        select(Approval).where(
            Approval.ticket_id == ticket.id,
            Approval.stage_key == stage_key,
            Approval.status == ApprovalStatus.PENDING,
            Approval.kind == ApprovalKind.WORKFLOW_GATE,
        )
    ).first()
    if existing:
        return existing
    checklist = expand_gate_checklist_for_ticket(
        session, ticket, list(stage_def.checklist) if stage_def else []
    )
    allowed = allowed_resolution_actions(list(human_required_actions))
    approval = Approval(
        ticket_id=ticket.id,
        workspace_id=ticket.workspace_id,
        kind=ApprovalKind.WORKFLOW_GATE,
        run_id=run_id,
        title=title or f"Resolve {stage_name} exit actions",
        level="high" if ticket.priority == 1 else "medium",
        stage_key=stage_key,
        impact=impact or build_gate_impact(ticket, stage_name),
        checklist_json=json.dumps(checklist),
        tool_input_json=json.dumps(
            {
                "human_required_actions": [
                    action.model_dump(mode="json") for action in human_required_actions
                ],
                "allowed_actions": [action.value for action in allowed],
            }
        ),
        response_json=ExitActionGateLedger(
            settled_action_keys=list(settled_action_keys or [])
        ).model_dump_json(),
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
    comes after the work, as unresolved exit actions raise it). Otherwise the approval
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


def _latest_succeeded_run(session: Session, ticket: Ticket, stage_key: str) -> AgentRun | None:
    return session.exec(
        select(AgentRun)
        .where(
            AgentRun.ticket_id == ticket.id,
            AgentRun.stage_key == stage_key,
            AgentRun.status == RunStatus.SUCCEEDED,
        )
        .order_by(col(AgentRun.created_at).desc())
    ).first()


def gate_on_orchestrator_pass(
    session: Session, ticket: Ticket, stage: WorkflowStageDef
) -> Approval | None:
    """The gate an orchestrator's ``complete_stage`` pass must raise, or None.

    An orchestrator can pass a stage that no agent run of its own completed —
    an external driver advancing a gate stage directly. Advancing then skipped
    every exit action, so an operator-judgment stage finished with nobody
    asked. The stage's outstanding actions are resolved here exactly as
    `request_exit_action_gate` resolves them; a pending gate already raised
    for the stage is returned rather than duplicated.
    """
    run = _latest_succeeded_run(session, ticket, stage.key)
    outstanding, settled = outstanding_exit_actions(session, run, stage)
    if not outstanding:
        return None
    # A person's approval would finish an agent stage no agent ran; refuse the
    # pass rather than open a gate nobody may approve.
    reason = gate_would_skip_work(session, ticket, stage)
    if reason:
        raise ValueError(reason)
    return create_workflow_gate_approval(
        session,
        ticket,
        stage.key,
        stage.name,
        stage_def=stage,
        human_required_actions=outstanding,
        settled_action_keys=settled,
        run_id=run.id if run is not None else None,
    )


def _assigned_but_unattested(run: AgentRun | None, settled: list[str]) -> list[str]:
    """Actions the run's agent was given that no passing report has confirmed."""
    if run is None:
        return []
    assigned = json.loads(run.assigned_exit_action_keys_json or "[]")
    completed = set(json.loads(run.completed_exit_action_keys_json or "[]")) | set(settled)
    return [key for key in assigned if key not in completed]


def request_exit_action_gate(
    session: Session,
    ticket: Ticket,
    stage_key: str,
    *,
    title: str = "",
    impact: str = "",
) -> Approval:
    """A gate asked for over MCP, raised from the stage's exit actions — never blank.

    An agent may ask a person to look at a stage, but what the person may do is
    the resolver's call, not the caller's: the gate lists the stage's
    outstanding exit actions against the latest successful run, and is refused
    while that run's assigned actions lack a passing report — run completion
    raises a gate only after one. A stage with nothing outstanding has
    nothing to approve, and a blank gate is refused rather than opened, because
    approving one would mark the stage done on no one's evidence.
    """
    stage = ticket_stage_definition(session, ticket, stage_key)
    if stage is None:
        raise ValueError(f"Unknown stage key: {stage_key}")
    reason = gate_would_skip_work(session, ticket, stage)
    if reason:
        raise ValueError(reason)
    run = _latest_succeeded_run(session, ticket, stage.key)
    outstanding, settled = outstanding_exit_actions(session, run, stage)
    unattested = _assigned_but_unattested(run, settled)
    if unattested:
        # Run completion opens a gate only after a passing report; asking for one
        # after a run that did not pass would let a person's approval finish the
        # stage over actions its agent was given and never confirmed.
        raise ValueError(
            f"Stage '{stage.key}' assigned {', '.join(unattested)} to its agent, and no "
            "passing report attests them. A gate cannot stand in for that work: re-run "
            "the stage."
        )
    if not outstanding:
        raise ValueError(
            f"Stage '{stage.key}' has no unresolved exit actions, so there is nothing for a "
            "person to approve. Finish it with complete_stage; a gate lists the exit actions "
            "a person must resolve."
        )
    approval = create_workflow_gate_approval(
        session,
        ticket,
        stage.key,
        stage.name,
        stage_def=stage,
        human_required_actions=outstanding,
        settled_action_keys=settled,
        run_id=run.id if run is not None else None,
        title=title,
        impact=impact,
    )
    if approval is None:
        raise ValueError(f"Stage '{stage.key}' raised no gate")
    instance = workflow_instance_for(session, ticket.id)
    _, stages = resolve_ticket_stages(session, ticket)
    if instance is not None and stages:
        set_stage_status(ticket, instance, stages, stage.key, StageStatus.AWAITING)
        session.add(instance)
        session.add(ticket)
        session.commit()
    return approval
