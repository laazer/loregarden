"""Detector: a ticket blocked because a start met a run already doing its work.

Kept apart from `child_run_wait` (the guard) so the invariant registry can
import it without pulling the orchestrator: `invariants` reaches
`builtin_orchestrator` through `workflow_monitor`.
"""

from __future__ import annotations

from loregarden.models.domain import OrchestrationRun, Ticket, TicketState
from loregarden.services.invariants import Violation
from loregarden.services.run_concurrency import ALREADY_RUNNING_PREFIX
from sqlmodel import Session, col, select


def tickets_blocked_by_a_live_run_refusal(session: Session) -> list[Violation]:
    """Tickets blocked because a start of theirs, or of a child, met a live run.

    The refusal is never the ticket's fault: some run was already doing the
    work. The child pass above waits instead of raising, so a ticket blocked
    this way means a new caller let the refusal reach a run boundary. Entered
    at the finish of the latest run that recorded the refusal.
    """
    blocked = session.exec(
        select(Ticket)
        .where(Ticket.state == TicketState.BLOCKED)
        .where(col(Ticket.blocking_issues).startswith(ALREADY_RUNNING_PREFIX))
    ).all()
    violations: list[Violation] = []
    for ticket in blocked:
        refused = session.exec(
            select(OrchestrationRun)
            .where(OrchestrationRun.ticket_id == ticket.id)
            .where(col(OrchestrationRun.error_message).startswith(ALREADY_RUNNING_PREFIX))
            .order_by(col(OrchestrationRun.created_at).desc())
        ).first()
        violations.append(
            Violation(
                subject_id=ticket.id,
                ticket_id=ticket.id,
                entered_at=refused.finished_at if refused else None,
                detail=f"{ticket.external_id} is blocked on {ticket.blocking_issues}",
            )
        )
    return violations
