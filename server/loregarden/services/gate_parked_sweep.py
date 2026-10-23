"""Tickets an exhausted transition gate left parked at a stage nothing will run.

Until 2026-10-23, `gate_recovery._block_after_gate_failure` rerouted the stage
back to itself, left the ticket in progress and ended the orchestration
SUCCEEDED, with no automatic pass left to run. The ticket then sat at a pending
stage with nothing scheduled: 36 orchestration runs ended that way, and two
tickets were still parked when it was fixed, the older for two months
(lg-milestone-that-1216 was the case that surfaced it). The writer now blocks
through `OrchestrationCallbackService.block_ticket`; this finds the rows it left
behind and blocks them the same way, so the operator sees the block and a
requeue runs the stage again. The block's kind is recorded by the
`every_block_is_settled` invariant on the same pass.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from loregarden.models.domain import (
    OrchestrationRun,
    OrchestrationRunStatus,
    StageStatus,
    Ticket,
    TicketState,
)
from loregarden.services.orchestration import OrchestrationService
from loregarden.services.ticket_state_service import InvalidTicketTransition, choose
from loregarden.services.workflow_service import resolve_ticket_stages
from loregarden.services.workflow_state import set_stage_status
from sqlmodel import Session, col, select

logger = logging.getLogger(__name__)

#: How the old writer ended the orchestration. Nothing writes it any more; it
#: is how the rows that writer left behind are recognised.
PARKED_MESSAGE = "Transition gate failed at '{stage}'; rerouted for rework"

#: What the ticket says once blocked: the writer's own pointer.
PARKED_POINTER = "Transition gate failed at '{stage}' — see the Timeline tab for details."

_SETTLED = (TicketState.BLOCKED, TicketState.DONE, TicketState.WONT_DO)


@dataclass(frozen=True)
class ParkedTicket:
    ticket: Ticket
    run: OrchestrationRun


def parked_by_gate(session: Session) -> list[ParkedTicket]:
    """Tickets whose latest orchestration ended that way and that have not moved since.

    Still at the same stage, still pending, not blocked or closed, and no
    orchestration started after it: a ticket someone requeued, restarted or
    finished since is not parked.
    """
    runs = session.exec(
        select(OrchestrationRun)
        .where(OrchestrationRun.status == OrchestrationRunStatus.SUCCEEDED)
        .where(col(OrchestrationRun.error_message).like("Transition gate failed at %"))
        .order_by(col(OrchestrationRun.created_at).desc())
    ).all()
    found: list[ParkedTicket] = []
    seen: set[str] = set()
    for run in runs:
        if run.ticket_id in seen:
            continue
        seen.add(run.ticket_id)
        ticket = session.get(Ticket, run.ticket_id)
        if ticket is None or ticket.state in _SETTLED:
            continue
        if run.error_message != PARKED_MESSAGE.format(stage=ticket.workflow_stage_key):
            continue
        if ticket.workflow_stage_status != StageStatus.PENDING:
            continue
        if _started_since(session, run):
            continue
        found.append(ParkedTicket(ticket=ticket, run=run))
    return found


def _started_since(session: Session, run: OrchestrationRun) -> bool:
    newer = session.exec(
        select(OrchestrationRun.id)
        .where(OrchestrationRun.ticket_id == run.ticket_id)
        .where(col(OrchestrationRun.created_at) > run.created_at)
    ).first()
    return newer is not None


def block_parked(session: Session, parked: list[ParkedTicket]) -> int:
    """Block each one at its stage, as the writer does now. Returns how many moved."""
    orch = OrchestrationService(session)
    moved = 0
    for item in parked:
        ticket = item.ticket
        stage = ticket.workflow_stage_key
        try:
            choose(session, ticket, TicketState.BLOCKED, actor="reconcile", emit=False)
        except InvalidTicketTransition:
            logger.warning(
                "%s is parked at '%s' by an exhausted gate but cannot move to blocked from %s; "
                "left for a person",
                ticket.external_id,
                stage,
                ticket.state,
                exc_info=True,
            )
            continue
        instance = orch.get_workflow_instance(ticket.id)
        _, stages = resolve_ticket_stages(session, ticket)
        if instance is not None and stages:
            set_stage_status(ticket, instance, stages, stage, StageStatus.BLOCKED)
            session.add(instance)
        ticket.blocking_issues = PARKED_POINTER.format(stage=stage)
        ticket.next_status = "Blocked"
        session.add(ticket)
        moved += 1
    session.commit()
    return moved
