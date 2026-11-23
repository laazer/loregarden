"""Invariant: a pending stage-park approval's stage is still parked.

Kept apart from `stale_stage_parks` (the guard), which `approval_ignore` and the
orchestrator import; the invariant machinery reaches the orchestrator, so the
guard must not import it back.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date

from loregarden.models.domain import Approval, ApprovalKind, ApprovalStatus, InvariantReentry
from loregarden.services.invariants import Invariant, Violation
from loregarden.services.stale_stage_parks import announce_closed, close_park, is_stale_park
from sqlmodel import Session, col, select


def stale_parks(session: Session) -> list[Violation]:
    """Pending parks whose stage is no longer AWAITING."""
    pending = session.exec(
        select(Approval).where(
            Approval.kind == ApprovalKind.STAGE_PARK,
            Approval.status == ApprovalStatus.PENDING,
            col(Approval.ticket_id).is_not(None),
        )
    ).all()
    return [
        Violation(
            subject_id=approval.id,
            ticket_id=approval.ticket_id,
            # When it went stale is not recorded; the park itself is the bound.
            entered_at=approval.created_at,
            detail=f"park {approval.id} on stage '{approval.stage_key}' outlived its stage",
        )
        for approval in pending
        if is_stale_park(session, approval)
    ]


def close_stale_park_violations(session: Session, violations: Sequence[Violation]) -> int:
    """Close each violation's park if it is still stale; re-checks each one."""
    closed: list[Approval] = []
    for violation in violations:
        approval = session.get(Approval, violation.subject_id)
        if approval is None or not is_stale_park(session, approval):
            continue
        close_park(session, approval, "Closed by reconcile: its stage is no longer parked")
        closed.append(approval)
    if closed:
        session.commit()
        for approval in closed:
            announce_closed(session, approval)
    return len(closed)


STALE_PARKS_ARE_CLOSED = Invariant(
    name="stale_parks_are_closed",
    ticket="lg-workflow-integrity-2759",
    statement="a pending stage-park approval's stage is AWAITING",
    detect=stale_parks,
    repair=close_stale_park_violations,
    reentry=InvariantReentry.EXPECTED,
    since=date(2026, 11, 22),
    expected_because=(
        "a requeue, or a ticket moved by hand, takes a parked stage out of AWAITING without "
        "dispatching it, and nothing on those paths answers the park; the dispatch that "
        "follows closes it, and this closes it sooner"
    ),
)
