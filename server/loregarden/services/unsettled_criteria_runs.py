"""Detector: a ticket finished with acceptance criteria nobody answered for.

Kept apart from `criterion_gate` (the guard) for the reason
`undecided_finding_runs` is: the orchestrator imports the guard, and the
invariant machinery reaches the orchestrator through `workflow_monitor`.
"""

from __future__ import annotations

from datetime import datetime

from loregarden.models.domain import OrchestrationRun, Ticket, TicketState
from loregarden.services.criterion_gate_stages import runs_check
from loregarden.services.criterion_ledger import delegation_gaps, unsettled
from loregarden.services.invariants import Violation
from loregarden.services.workflow_service import resolve_ticket_stages
from sqlmodel import Session, col, select

#: When the check before the gate landed. Tickets done before it were never
#: asked to settle their criteria, and recording them as met now would invent
#: evidence, so only tickets driven since are held to it.
CHECKED_SINCE = datetime(2026, 11, 7)


def _checked_by_workflow(session: Session, ticket: Ticket) -> bool:
    """Whether this ticket's workflow runs the check: it has a stage that settles criteria."""
    _, stages = resolve_ticket_stages(session, ticket)
    return runs_check(stages)


def done_tickets_with_unsettled_criteria(session: Session) -> list[Violation]:
    """Done tickets, driven since the check landed, with a criterion not answered for.

    The check refuses such a ticket on every path that starts or finishes the
    stage it guards (`gate_dispatch_check`), so one here was finished by a path
    that test missed, had a criterion added after it passed, or had a delegate
    dropped after it finished. Entered at the ticket's last update,
    the closest record of when it finished.
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
        if not _checked_by_workflow(session, ticket):
            continue
        open_criteria = unsettled(session, ticket) + [
            g.criterion for g in delegation_gaps(session, ticket)
        ]
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
