"""Invariant findings at their live volume.

One invariant violated by many tickets records the same sentence on each, and
only the evidence says what is wrong with which ticket. The monitor once listed
38 of them as 38 cards of one sentence, with none of that evidence on screen.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta, timezone

from pydantic import BaseModel
from sqlmodel import Session

from loregarden.models.domain import MonitorArtifactKind, TicketState
from loregarden.testing.factories import make_artifact

_EPOCH = datetime(2026, 11, 6, tzinfo=timezone.utc)


class InvariantFindingShape(BaseModel):
    """How many tickets one invariant names, by whether they are finished."""

    condition: str
    invariant: str
    summary: str
    live: int
    finished: int


#: Measured 2026-11-23 over 670 findings: the two invariants naming the most
#: tickets, out of 568 findings on tickets (79 on live ones). Live, 348 finished
#: tickets carry the second one; the scenario holds fewer finished tickets.
INVARIANT_FINDINGS = [
    InvariantFindingShape(
        condition="invariant_unrepaired",
        invariant="tickets_are_created_under_the_size_caps",
        summary=(
            "'no ticket is created over a write-time size cap' is violated, and nothing repairs "
            "it automatically. Split the ticket the finding names: file a sibling under the same "
            "parent and depends_on set to it, move the overflowing criteria or description text "
            "onto the sibling, then shorten this one."
        ),
        live=38,
        finished=56,
    ),
    InvariantFindingShape(
        condition="invariant_reentered",
        invariant="land_verification_ran",
        summary=(
            "'a landed ticket was verified before it landed' was violated again after its guard "
            "landed; the test guarding it does not cover the writer that did this."
        ),
        live=0,
        finished=300,
    ),
]


def add_invariant_findings(
    session: Session, tickets: list, make_id: Callable[[str, int], str]
) -> int:
    finished = [t for t in tickets if t[3] in (TicketState.DONE, TicketState.WONT_DO)]
    live = [t for t in tickets if t[3] not in (TicketState.DONE, TicketState.WONT_DO)]
    count = 0
    for shape in INVARIANT_FINDINGS:
        for _slug, ticket_id, ext, _state, _parent in [
            *live[len(live) - shape.live :],
            *finished[len(finished) - shape.finished :],
        ]:
            over = 1 + count % 9
            first = _EPOCH + timedelta(minutes=count)
            make_artifact(
                session,
                artifact_id=make_id("invariant-finding", count),
                ticket_id=ticket_id,
                kind=MonitorArtifactKind.FINDING.value,
                title=f"{shape.condition}:{shape.invariant}",
                content={
                    "condition": shape.condition,
                    "stage_key": shape.invariant,
                    "summary": shape.summary,
                    "evidence": {
                        "invariant": shape.invariant,
                        "subject_id": ticket_id,
                        "detail": f"{ext} is {over} over its limit of 12",
                    },
                    "occurrences": 16000 + count,
                    "first_seen": first.isoformat(),
                    "last_seen": (first + timedelta(days=17)).isoformat(),
                },
            )
            count += 1
    return count
