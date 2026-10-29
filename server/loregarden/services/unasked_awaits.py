"""A stage `awaiting` a person has a pending approval asking that person.

`awaiting` is the one stage status that says the next move is a person's, and
the inbox is the only place a person finds that move. A stage parked there with
no pending approval waits forever on someone nobody asked: Work items lists it
under "Needs me", Home's inbox has nothing for it, and nothing on the timer
moves it. On the 2026-10-27 snapshot three tickets were in that state
(blob-mesh-surface-152/153/154, at `script_review` since 2026-10-02) with no
approval row, agent run or orchestration run ever written for them: the stage
was set by hand, through the manual edit's stage statuses, which accepted
`awaiting` like any other value.

Every writer that raises a gate writes the approval in the same commit as the
stage or before it. The manual edit now refuses `awaiting` unless the stage
already has its pending approval (`refuse_unasked_awaits`); the rows already in
the state are blocked and settled here, so the operator sees a block with a
reason and a requeue runs the stage.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterable
from dataclasses import dataclass

from loregarden.db.closed_workflow_guard import CLOSED_STATES
from loregarden.models.domain import (
    Approval,
    ApprovalStatus,
    StageStatus,
    Ticket,
    TicketState,
    WorkflowInstance,
)
from loregarden.services.block_settlement import settle_block
from loregarden.services.ticket_state_service import InvalidTicketTransition, choose
from loregarden.services.workflow_service import resolve_ticket_stages, workflow_instance_for
from loregarden.services.workflow_state import set_stage_status
from sqlmodel import Session, col, select

logger = logging.getLogger(__name__)

#: What the ticket says once blocked here.
UNASKED_POINTER = (
    "Stage '{stage}' was left awaiting a person, but no approval asks anyone: "
    "requeue the stage to run it, or set it done if the work landed elsewhere."
)


@dataclass(frozen=True)
class UnaskedAwait:
    ticket: Ticket
    stage_key: str


def _asked(session: Session, ticket_ids: Iterable[str]) -> set[tuple[str, str]]:
    """(ticket id, stage key) for every pending approval on these tickets."""
    ids = list(ticket_ids)
    if not ids:
        return set()
    rows = session.exec(
        select(Approval.ticket_id, Approval.stage_key).where(
            col(Approval.ticket_id).in_(ids), Approval.status == ApprovalStatus.PENDING
        )
    ).all()
    return {(ticket_id, stage_key) for ticket_id, stage_key in rows if ticket_id}


def refuse_unasked_awaits(session: Session, ticket: Ticket, stage_keys: Iterable[str]) -> None:
    """Raise ``ValueError`` if any of ``stage_keys`` has no pending approval.

    For writers that set a status they were handed rather than one they
    derived: `awaiting` is reached by raising an approval, never by naming it.
    """
    keys = sorted(set(stage_keys))
    if not keys:
        return
    asked = _asked(session, [ticket.id])
    unasked = [key for key in keys if (ticket.id, key) not in asked]
    if unasked:
        raise ValueError(
            f"Stage(s) {', '.join(unasked)} cannot be set to awaiting: no pending approval "
            "asks a person anything there, so nothing would ever move it. Raise one with "
            "loregarden_request_approval, or choose pending, blocked or done."
        )


def unasked_awaits(session: Session) -> list[UnaskedAwait]:
    """Every awaiting stage on an open ticket with no pending approval at it.

    Both records of it: the ticket's cursor, which is what Work items and the
    board read, and the stage map's rows. Closed tickets are
    `closed_tickets_have_closed_workflows`'s: their open stages close with them.
    """
    open_tickets = col(Ticket.state).not_in(list(CLOSED_STATES))
    cursors = session.exec(
        select(Ticket).where(open_tickets, Ticket.workflow_stage_status == StageStatus.AWAITING)
    ).all()
    mapped = session.exec(
        select(Ticket, WorkflowInstance)
        .join(WorkflowInstance, col(WorkflowInstance.ticket_id) == col(Ticket.id))
        .where(open_tickets)
        .where(col(WorkflowInstance.stages_json).contains(f'"{StageStatus.AWAITING.value}"'))
    ).all()
    candidates: dict[tuple[str, str], Ticket] = {
        (ticket.id, ticket.workflow_stage_key): ticket for ticket in cursors
    }
    for ticket, instance in mapped:
        for row in json.loads(instance.stages_json or "[]"):
            if row["status"] == StageStatus.AWAITING.value:
                candidates[(ticket.id, row["key"])] = ticket
    asked = _asked(session, {ticket_id for ticket_id, _ in candidates})
    return [
        UnaskedAwait(ticket=ticket, stage_key=stage_key)
        for (ticket_id, stage_key), ticket in sorted(candidates.items())
        if (ticket_id, stage_key) not in asked
    ]


def block_unasked_awaits(session: Session, items: list[UnaskedAwait]) -> int:
    """Block each at its stage, settled with no repair turn. Returns how many moved.

    Blocked rather than re-asked: nothing records what the person was meant to
    decide, so an approval raised here would ask a question nobody wrote. A
    block names the stage, and a requeue runs it.
    """
    moved = 0
    for item in items:
        ticket = item.ticket
        stage = item.stage_key
        if ticket.state != TicketState.BLOCKED:
            try:
                choose(session, ticket, TicketState.BLOCKED, actor="reconcile", emit=False)
            except InvalidTicketTransition:
                logger.warning(
                    "%s awaits a person at '%s' with no approval but cannot move to blocked "
                    "from %s; left for a person",
                    ticket.external_id,
                    stage,
                    ticket.state,
                    exc_info=True,
                )
                continue
        instance = workflow_instance_for(session, ticket.id)
        _, stages = resolve_ticket_stages(session, ticket)
        if instance is not None and stages:
            ticket.workflow_stage_key = stage
            set_stage_status(ticket, instance, stages, stage, StageStatus.BLOCKED)
            session.add(instance)
        ticket.blocking_issues = UNASKED_POINTER.format(stage=stage)
        ticket.next_status = "Blocked"
        session.add(ticket)
        settle_block(
            session,
            ticket,
            stage_key=stage,
            message=ticket.blocking_issues,
            instance=instance,
            stages=stages,
            repairable=False,
            no_repair_reason="no record says what the person was meant to decide",
        )
        moved += 1
    session.commit()
    return moved
