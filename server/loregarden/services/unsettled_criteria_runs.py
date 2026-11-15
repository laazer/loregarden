"""Detector: a ticket finished with acceptance criteria nobody answered for.

Kept apart from `criterion_gate` (the guard) for the reason
`undecided_finding_runs` is: the orchestrator imports the guard, and the
invariant machinery reaches the orchestrator through `workflow_monitor`.
"""

from __future__ import annotations

from loregarden.models.domain import OrchestrationRun, Ticket, TicketState
from loregarden.services.criteria_answered import CHECKED_SINCE, criteria_left_unanswered
from loregarden.services.invariants import Violation
from sqlmodel import Session, col, select


def done_tickets_with_unsettled_criteria(session: Session) -> list[Violation]:
    """Done tickets, driven since the check landed, with a criterion not answered for.

    The check refuses such a ticket on every path that starts or finishes the
    stage it guards (`gate_dispatch_check`), and the rollup holds a parent open
    over its own (`ticket_rollup`), so one here was finished by a path neither
    covers, had a criterion added after it passed, or had a delegate dropped
    after it finished. Entered at the ticket's last update, the closest record
    of when it finished.
    """
    tickets = session.exec(
        select(Ticket)
        .join(OrchestrationRun, col(OrchestrationRun.ticket_id) == Ticket.id)
        .where(Ticket.state == TicketState.DONE)
        .where(col(OrchestrationRun.started_at) >= CHECKED_SINCE)
        .where(Ticket.acceptance_criteria_json != "[]")
        .distinct()
    ).all()
    violations: list[Violation] = []
    for ticket in tickets:
        open_criteria = criteria_left_unanswered(session, ticket)
        if open_criteria:
            violations.append(
                Violation(
                    subject_id=ticket.id,
                    ticket_id=ticket.id,
                    entered_at=ticket.updated_at,
                    detail=f"{ticket.external_id} is done with {len(open_criteria)} criteria not answered for",
                )
            )
    return violations
