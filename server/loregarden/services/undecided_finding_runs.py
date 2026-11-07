"""Detector: an undecided finding a drive was started on anyway.

Kept apart from `discovered_work` (the guard) so the orchestrator can import
the guard without pulling the invariant machinery: `invariants` reaches
`builtin_orchestrator` through `workflow_monitor`.
"""

from __future__ import annotations

from loregarden.core.state_machine import StateMachine
from loregarden.models.domain import OrchestrationRun, Ticket, TicketDiscovery
from loregarden.services.invariants import Violation
from sqlalchemy import func
from sqlmodel import Session, col, select


def undecided_findings_that_ran(session: Session) -> list[Violation]:
    """Open undecided findings a drive was started on after they were filed.

    `refuse_undecided` stops every start, so one here means a start path that
    does not go through `start_orchestration_run`. Entered at the first such
    run's start.
    """
    rows = session.exec(
        select(TicketDiscovery, Ticket, func.min(OrchestrationRun.started_at))
        .join(Ticket, col(Ticket.id) == TicketDiscovery.filed_ticket_id)
        .join(OrchestrationRun, col(OrchestrationRun.ticket_id) == Ticket.id)
        .where(col(TicketDiscovery.decision).is_(None))
        .where(col(Ticket.state).not_in(StateMachine.TERMINAL_TICKET_STATES))
        .where(col(OrchestrationRun.started_at) > TicketDiscovery.filed_at)
        .group_by(col(TicketDiscovery.id))
    ).all()
    return [
        Violation(
            subject_id=ticket.id,
            ticket_id=ticket.id,
            entered_at=started,
            detail=f"{ticket.external_id} ran before its scope was decided ({ticket.state.value})",
        )
        for _, ticket, started in rows
    ]
