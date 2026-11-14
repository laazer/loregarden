"""`settled_holds_read_blocked`'s detector and repair, as invariant callables.

Apart from `queue_repair_settle` because `queue_lanes` imports that module, and
the invariant runtime (`services.invariants`) reaches `queue_lanes` in turn.
"""

from __future__ import annotations

from collections.abc import Sequence

from loregarden.services.invariants import Violation
from loregarden.services.queue_repair_settle import reblock_settled, settled_holds_left_rearmed
from sqlmodel import Session


def settled_hold_violations(session: Session) -> list[Violation]:
    """`settled_holds_read_blocked`'s detector."""
    return [
        Violation(
            subject_id=ticket.id,
            ticket_id=ticket.id,
            entered_at=entry.last_failed_at,
            detail=f"{ticket.external_id} reads in progress on '{ticket.workflow_stage_key}' "
            f"with nothing running it: its {entry.repair_route} repair hold ended "
            f"({entry.failure_reason})",
        )
        for entry, ticket in settled_holds_left_rearmed(session)
    ]


def reblock_settled_violations(session: Session, violations: Sequence[Violation]) -> int:
    """`settled_holds_read_blocked`'s repair: re-block each still-stranded ticket."""
    named = {v.ticket_id for v in violations}
    return sum(
        1
        for entry, ticket in settled_holds_left_rearmed(session)
        if ticket.id in named and reblock_settled(session, entry, ticket)
    )
