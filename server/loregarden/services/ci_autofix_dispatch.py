"""Reopen a done ticket for one CI-fix turn, or block it when its budget is spent.

CI runs after a ticket's work is published, so a red check usually lands on a
ticket that is already `done`. This uses the same move the landing resolver
uses for a conflict (`landing._offer_conflict_resolution`): the ticket goes
back to its terminal stage with the `repair` agent pinned and the failure as
its brief. When the turn passes, the terminal stage lands the ticket again,
and `publish_tree` pushes the new tip to the pull request, so CI runs again on
the fix. Nothing here pushes or touches git.

A done ticket has no orchestration left to run that stage, so this starts one
through the slot pool (`admit_resumes`), as the network-wait resume does. In a
full pool the ticket waits in a lane; it is not dropped.

Only a ticket that publishes its own branch is handled here. A tree root's
pull request is on its integration branch, which the root's own worktree does
not commit to, so a repair turn there would fix nothing the PR contains. The
caller refuses that case before it gets here, and says why.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from loregarden.agents.registry import REPAIR_AGENT_ID, get_agent
from loregarden.config import settings
from loregarden.core.state_machine import StateMachine
from loregarden.core.workflow_terminal import find_terminal_stage
from loregarden.models.domain import (
    OrchestratorDecision,
    StageStatus,
    Ticket,
    TicketState,
    WorkflowInstance,
    WorkflowStageDef,
)
from loregarden.services.artifact_service import block_ticket_for_unresolved_blocker
from loregarden.services.orchestration import OrchestrationService
from loregarden.services.orchestration_callbacks import OrchestrationCallbackService
from loregarden.services.orchestration_recovery import (
    InterruptionResume,
    admit_resumes,
    latest_orchestration_run,
    schedule_interrupted_resumes,
)
from loregarden.services.orchestrator_decisions import record_orchestrator_decision
from loregarden.services.repair_brief import arm_repair_turn
from loregarden.services.requeue import requeue_stage
from loregarden.services.workflow_service import resolve_ticket_stages, workflow_instance_for
from loregarden.services.workflow_state import set_stage_status
from sqlmodel import Session

logger = logging.getLogger(__name__)

#: Who the ticket's history says reopened it.
CI_ACTOR = "ci-autofix"


@dataclass(frozen=True)
class CIFixDispatch:
    """Whether the fix turn was armed and scheduled; `detail` says why when it was not."""

    ok: bool
    detail: str = ""


@dataclass(frozen=True)
class _Workflow:
    instance: WorkflowInstance
    stages: list[WorkflowStageDef]
    terminal_key: str


def _workflow(session: Session, ticket: Ticket) -> _Workflow | None:
    instance = workflow_instance_for(session, ticket.id)
    _, stages = resolve_ticket_stages(session, ticket)
    terminal = find_terminal_stage(stages)
    if instance is None or terminal is None:
        return None
    return _Workflow(instance=instance, stages=stages, terminal_key=terminal.key)


def _reopen(session: Session, ticket: Ticket, workflow: _Workflow, reason: str) -> WorkflowInstance:
    """Back to the terminal stage, PENDING, in the backlog. Returns the fresh instance."""
    requeue_stage(
        session,
        OrchestrationService(session),
        ticket,
        stage_key=workflow.terminal_key,
        reason=reason,
        actor=CI_ACTOR,
        state=TicketState.BACKLOG,
    )
    return workflow_instance_for(session, ticket.id) or workflow.instance


def dispatch_ci_fix(
    session: Session,
    ticket: Ticket,
    *,
    brief: str,
    reason: str,
    evidence: dict[str, Any],
) -> CIFixDispatch:
    """Reopen ``ticket`` at its terminal stage with a repair turn, and start it."""
    if get_agent(REPAIR_AGENT_ID) is None:
        # Pinning an agent that cannot run would reopen the ticket for nothing.
        return CIFixDispatch(False, f"the '{REPAIR_AGENT_ID}' agent is not registered")
    if OrchestrationCallbackService(session).get_active_orchestration_run(ticket.id):
        return CIFixDispatch(False, "an orchestration is already running on this ticket")
    workflow = _workflow(session, ticket)
    if workflow is None:
        return CIFixDispatch(False, "the ticket has no workflow with a terminal stage to reopen")

    previous = latest_orchestration_run(session, ticket)
    instance = _reopen(session, ticket, workflow, reason)
    template, _ = resolve_ticket_stages(session, ticket)
    transitions = StateMachine.parse_transitions(template.transitions_json) if template else []
    arm_repair_turn(
        session,
        ticket,
        instance,
        workflow.stages,
        transitions,
        stage_key=workflow.terminal_key,
        brief=brief,
    )
    session.add(ticket)
    session.add(instance)
    record_orchestrator_decision(
        session,
        ticket,
        decision=OrchestratorDecision.DISPATCHED_CI_FIX,
        stage_key=workflow.terminal_key,
        reason=reason,
        evidence=evidence,
    )
    session.commit()

    plan = InterruptionResume(
        ticket_id=ticket.id,
        # Carried from whoever drove the ticket last: a ticket an operator
        # approved step by step is not promoted to unattended by a red check.
        auto_approve=previous.auto_approve if previous else False,
        approve_design_plans=previous.approve_design_plans if previous else True,
        auto_repair=previous.auto_repair if previous else True,
        stop_at_stage_key=workflow.terminal_key,
        timeout_seconds=settings.ci_auto_fix_timeout,
    )
    requests, _ = admit_resumes(session, [(ticket, plan)])
    schedule_interrupted_resumes(requests)
    logger.warning(
        "CI failed on %s; reopened at '%s' for a fix turn (%s)",
        ticket.external_id,
        workflow.terminal_key,
        "started" if requests else "queued for a lane",
    )
    return CIFixDispatch(True)


def block_for_ci(session: Session, ticket: Ticket, message: str) -> CIFixDispatch:
    """Reopen ``ticket`` and block it at its terminal stage, for a person.

    For CI that is still red once the fix budget is spent. Leaving the ticket
    `done` would show finished work beside a pull request that cannot merge.
    """
    workflow = _workflow(session, ticket)
    if workflow is None:
        return CIFixDispatch(False, "the ticket has no workflow with a terminal stage to block")
    instance = _reopen(session, ticket, workflow, message)
    set_stage_status(ticket, instance, workflow.stages, workflow.terminal_key, StageStatus.BLOCKED)
    session.add(instance)
    block_ticket_for_unresolved_blocker(
        session, ticket, entry=message, stage_key=workflow.terminal_key
    )
    logger.warning("Blocked %s for a person: %s", ticket.external_id, message)
    return CIFixDispatch(True)
