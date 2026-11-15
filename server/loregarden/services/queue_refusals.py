"""Why a queued entry did not start, recorded once per reason.

The reconcile pass retries every idle lane with work in it every 30 seconds, so a
head that is refused for a standing reason (an external harness drives its ticket,
the ticket holds another lane, the process is draining) is refused again on every
pass. Re-stamping the same reason each time was a write transaction per wedged lane
per pass that told nobody anything new, and on SQLite every write takes the file
lock. So a refusal is written when its reason changes, and `last_failed_at` stays
the moment the entry first met that reason, which is what an invariant's
`entered_at` reads it as.

Kept apart from `queue_lanes` and `queue_dispatch` because both refuse entries,
and `queue_lanes` must not import `queue_dispatch` (see that module's docstring).
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from loregarden.models.domain import (
    AgentSlot,
    OrchestrationDriver,
    OrchestrationRun,
    QueuedRun,
    QueueEntryKind,
)
from loregarden.services.parallel_queue import LIVE_ORCHESTRATION_STATUSES, tickets_holding_lanes
from sqlmodel import Session, col, select

logger = logging.getLogger(__name__)


def record_refusal(session: Session, entry: QueuedRun, reason: str, *, commit: bool = True) -> bool:
    """Write `reason` onto the entry unless it already says exactly that.

    Returns whether anything was written. The entry keeps its status either way;
    only the board's explanation changes.
    """
    if entry.failure_reason == reason:
        return False
    entry.failure_reason = reason
    entry.last_failed_at = datetime.now(timezone.utc)
    session.add(entry)
    if commit:
        session.commit()
    return True


def live_external_orchestration(session: Session, ticket_id: str) -> OrchestrationRun | None:
    """The external-harness orchestration currently driving this ticket, if any."""
    return session.exec(
        select(OrchestrationRun)
        .where(OrchestrationRun.ticket_id == ticket_id)
        .where(OrchestrationRun.driver == OrchestrationDriver.EXTERNAL_MCP)
        .where(col(OrchestrationRun.status).in_(LIVE_ORCHESTRATION_STATUSES))
    ).first()


def external_owner_reason(owner: OrchestrationRun) -> str:
    """The refusal recorded when an external harness is driving the ticket."""
    return (
        f"external orchestration {owner.id} is driving this ticket; "
        "the builtin dispatcher does not race it"
    )


def refused_before_claim(session: Session, head: QueuedRun, slot_number: int) -> bool:
    """Record a refusal that is a read, not a dispatch, and say whether one applied.

    Asked before a lane is claimed, so a head refused for a standing reason costs
    no claim and release. Only for a free lane: behind a busy one the head is
    simply waiting, and nothing should be written about it.
    """
    lane_is_free = session.exec(
        select(AgentSlot.id)
        .where(AgentSlot.slot_number == slot_number)
        .where(AgentSlot.is_available == True)  # noqa: E712
    ).first()
    if lane_is_free is None:
        return False
    if refuse_second_lane(session, head, slot_number):
        return True
    if head.entry_kind is not QueueEntryKind.STAGE:
        return False
    owner = live_external_orchestration(session, head.ticket_id)
    if owner is None:
        return False
    if record_refusal(session, head, external_owner_reason(owner)):
        logger.warning(
            "Lane %d stage dispatch skipped for ticket %s: orchestration %s is driving it "
            "from an external harness",
            slot_number,
            head.ticket_id,
            owner.id,
        )
    return True


def refuse_second_lane(session: Session, head: QueuedRun, slot_number: int) -> bool:
    """645: a ticket holding one lane must not take a second, whichever column
    recorded the first and whichever way this one would be recorded.

    `exclude_slot` keeps a caller that already claimed `slot_number` from
    finding its own claim and refusing itself.
    """
    elsewhere = tickets_holding_lanes(session, exclude_slot=slot_number).get(head.ticket_id)
    if not elsewhere:
        return False
    reason = f"ticket already occupies lane(s) {elsewhere}; one lane per ticket"
    if record_refusal(session, head, reason):
        logger.warning(
            "Lane %d refused for ticket %s: it already holds lane(s) %s",
            slot_number,
            head.ticket_id,
            elsewhere,
        )
    return True
