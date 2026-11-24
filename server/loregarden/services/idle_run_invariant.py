"""Invariant: a ticket whose last runs all did nothing is held, not waiting to be opened again.

Kept apart from `idle_orchestrations` (the guard's predicate), which
`run_service` imports; the invariant machinery reaches the orchestrator.
"""

from __future__ import annotations

from datetime import date

from loregarden.models.domain import (
    InvariantReentry,
    OrchestrationRun,
    OrchestrationRunStatus,
    Ticket,
)
from loregarden.services.idle_orchestrations import (
    IDLE_RUN_LIMIT,
    OPENABLE_STATES,
    idle_streak,
)
from loregarden.services.interruption_messages import ORCHESTRATION_LEASE_EXPIRED_PREFIX
from loregarden.services.invariants import Invariant, Violation
from sqlmodel import Session, col, select


def unheld_idle_tickets(session: Session) -> list[Violation]:
    """Entered when the run that reached the limit finished."""
    tickets = session.exec(
        select(Ticket)
        .where(col(Ticket.state).in_(OPENABLE_STATES))
        .where(
            col(Ticket.id).in_(
                select(OrchestrationRun.ticket_id)
                .where(OrchestrationRun.status == OrchestrationRunStatus.FAILED)
                .where(
                    col(OrchestrationRun.error_message).startswith(
                        ORCHESTRATION_LEASE_EXPIRED_PREFIX
                    )
                )
                .where(col(OrchestrationRun.last_seen_at).is_(None))
            )
        )
    ).all()
    found: list[Violation] = []
    for ticket in tickets:
        streak = idle_streak(session, ticket.id)
        if len(streak) < IDLE_RUN_LIMIT:
            continue
        found.append(
            Violation(
                subject_id=ticket.id,
                ticket_id=ticket.id,
                entered_at=streak[0].finished_at,
                detail=f"{ticket.external_id}: the last {IDLE_RUN_LIMIT} orchestration runs "
                f"({', '.join(run.run_code for run in streak)}) started no agent",
            )
        )
    return found


IDLE_TICKETS_ARE_HELD = Invariant(
    name="idle_tickets_are_held",
    ticket="lmkr-execution-leaves-11",
    statement=(
        "a ticket whose last two orchestration runs expired having started no agent is held"
    ),
    detect=unheld_idle_tickets,
    # Holding one late would stop a ticket whose cause is already fixed, and the
    # next run settles it either way: it works, or the sweep holds the ticket.
    repair=None,
    unrepaired_action=(
        "Start the ticket on a driver that runs agents, or attach the terminal agent "
        "it waits for; the report clears with its next run."
    ),
    reentry=InvariantReentry.DEFECT,
    # The day after the guard was written: the live server sweeps without it
    # until it restarts on the merged build.
    since=date(2026, 11, 24),
    prevented_by=(
        "server/tests/test_idle_orchestrations.py"
        "::test_the_second_idle_run_in_a_row_holds_the_ticket"
    ),
)
