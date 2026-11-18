"""Invariant: no answer that re-runs a stage is recorded on a closed ticket.

Kept apart from `approval_resolution` (the guard), which `orchestration`
imports; the invariant machinery reaches the orchestrator, so the guard must
not import it back.
"""

from __future__ import annotations

from datetime import date

from loregarden.models.domain import Approval, ApprovalStatus, InvariantReentry, Ticket
from loregarden.services.approval_resolution import STAGE_RERUNNING_APPROVAL_KINDS
from loregarden.services.invariants import Invariant, Violation
from loregarden.services.ticket_state_service import RESOLVED_STATES
from sqlmodel import Session, col, select


def answers_recorded_on_closed_tickets(session: Session) -> list[Violation]:
    """Approved or rejected re-running approvals resolved after their ticket closed.

    `refuse_rerunning_a_closed_ticket` refuses each before it is recorded, so
    one here is an answer whose stage never re-ran, on a ticket that may also
    have been revived from `wont_do`. Ignored rows are the legitimate way to
    clear such a card and are not counted. Entered at the approval's resolution.
    """
    rows = session.exec(
        select(Approval, Ticket)
        .join(Ticket, col(Ticket.id) == Approval.ticket_id)
        .where(col(Approval.kind).in_(STAGE_RERUNNING_APPROVAL_KINDS))
        .where(col(Approval.status).in_([ApprovalStatus.APPROVED, ApprovalStatus.REJECTED]))
        .where(col(Ticket.state).in_(RESOLVED_STATES))
        .where(col(Ticket.resolved_at).is_not(None))
        .where(col(Approval.resolved_at) > Ticket.resolved_at)
    ).all()
    return [
        Violation(
            subject_id=approval.id,
            ticket_id=ticket.id,
            entered_at=approval.resolved_at,
            detail=(
                f"{ticket.external_id} was {ticket.state.value} when its {approval.kind.value} "
                f"on '{approval.stage_key}' was answered; the answer never re-ran the stage"
            ),
        )
        for approval, ticket in rows
    ]


CLOSED_TICKETS_TAKE_NO_ANSWERS = Invariant(
    name="closed_tickets_take_no_stage_answers",
    ticket="lg-conversation-that-2733",
    statement="no answer that re-runs a stage is recorded on a done or wont_do ticket",
    detect=answers_recorded_on_closed_tickets,
    # Report-only: whether the answer still matters is the operator's call, and
    # reopening a ticket on their behalf would be the revival this prevents.
    repair=None,
    reentry=InvariantReentry.DEFECT,
    since=date(2026, 11, 18),
    prevented_by=(
        "server/tests/test_operator_questions.py"
        "::test_no_resolve_branch_records_the_row_and_then_fails_the_request"
    ),
    unrepaired_action=(
        "If the answer still matters, move the ticket to backlog and requeue the stage it "
        "names; otherwise leave it, since the ticket shipped without it"
    ),
)
