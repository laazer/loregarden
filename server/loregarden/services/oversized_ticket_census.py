"""Every ticket currently over a write-time size cap, as one detector.

The same function answers both questions a fix for a bad state has to answer
about the population: "how many are over the line today" (the count in the fix
coverage record) and "did one enter after the guard landed" (the
`tickets_are_created_under_the_size_caps` invariant). One detector rather than a
throwaway query plus a reconciler query, so the two numbers cannot drift apart.

Read-only. Repairing the existing population is lg-milestone-that-2251, this
ticket's dependent: only a person decides what a too-large ticket splits into,
and an automatic repair would truncate a description or delete criteria. That is
why the invariant is report-only.

`entered_at` is `created_at`, and that is a disclosed trade rather than an
oversight. `models/domain/tables.py` carries `revision: int` and no
ticket-content history, so this sees current size and never growth. `updated_at`
moves on exactly the shrinking and no-op edits the guard keeps legal, so most of
the 78 tickets that were already over the criteria cap would read as fresh
re-entry and escalate a false guard-failure bug every reconcile pass.
`created_at` under-reports an old ticket grown tomorrow, and reports correctly
the case `DEFECT` + `since` exists for: a ticket *created* over a cap after the
guard landed, i.e. a writer that walked around the create-path guard.
"""

from __future__ import annotations

from loregarden.models.domain import Ticket
from loregarden.services.acceptance_criteria import MAX_ACCEPTANCE_CRITERIA, load_criteria
from loregarden.services.invariants import Violation
from loregarden.services.ticket_growth_guard import MAX_DESCRIPTION_CHARS
from sqlmodel import Session, select


def _reasons(ticket: Ticket) -> list[str]:
    """Each write cap this ticket is over, and by how much."""
    over: list[str] = []
    criteria = len(load_criteria(ticket.acceptance_criteria_json))
    if criteria > MAX_ACCEPTANCE_CRITERIA:
        over.append(
            f"{criteria} acceptance criteria, {criteria - MAX_ACCEPTANCE_CRITERIA} over the "
            f"cap of {MAX_ACCEPTANCE_CRITERIA}"
        )
    chars = len(ticket.description or "")
    if chars > MAX_DESCRIPTION_CHARS:
        over.append(
            f"a {chars:,}-character description, {chars - MAX_DESCRIPTION_CHARS:,} over the "
            f"cap of {MAX_DESCRIPTION_CHARS:,}"
        )
    return over


def tickets_over_a_write_cap(session: Session) -> list[Violation]:
    """One violation per ticket over a write cap, naming which cap and by how much.

    A ticket over both caps is **one** violation naming both: one row in the bad
    state is one thing for a person to deal with, however many ways it is over.
    At the cap is not over it — 358 live tickets sit at exactly 12 criteria.
    """
    violations: list[Violation] = []
    for ticket in session.exec(select(Ticket)).all():
        over = _reasons(ticket)
        if not over:
            continue
        label = ticket.external_id or ticket.id
        violations.append(
            Violation(
                subject_id=ticket.id,
                ticket_id=ticket.id,
                entered_at=ticket.created_at,
                detail=f"{label} holds " + " and ".join(over),
            )
        )
    return violations
