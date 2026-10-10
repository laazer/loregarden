"""Sizing the shared slot pool: one slot per lane, numbered 1..N.

Split out of `ParallelQueueService` when a configurable lane count — and with
it the need to retire lanes, not only add them — put that class past its
1000-line cap. This is the piece that only ever looks at `agent_slots`
as a whole; claiming and releasing a slot stay with the queue.
"""

from __future__ import annotations

import logging
from uuid import uuid4

from loregarden.models.domain import AgentSlot
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

logger = logging.getLogger(__name__)


def fit_slot_pool(session: Session, size: int) -> None:
    """Fit the shared pool to `size` slots (idempotent).

    Adds what is missing, and retires *idle* slots numbered past the limit —
    what a lowered lane count, or migration 0058's merged pools, leaves
    behind. A slot with work in it is never deleted: it finishes, releases
    through whichever path its occupant ends on, and is retired by the next
    call here. Every claim reads the pool after one of these calls, so a
    retired lane cannot be handed new work in between.

    Read-then-insert is a race, and it was losing: two callers against an
    empty pool both saw nothing and both inserted a full set, so a limit of
    three became six slots and the admission gate stopped bounding anything.
    `slot_number` is unique as of migration 0083, so the loser's insert now
    fails instead of doubling the pool — it rolls back and re-reads, because
    the winner has by then created exactly what this was going to create.
    """
    for _ in range(2):
        try:
            _retire_surplus_slots(session, size)
            existing = session.exec(select(AgentSlot.slot_number)).all()
            taken = set(existing)
            missing = [n for n in range(1, size + 1) if n not in taken]
            if not missing:
                return

            for slot_num in missing:
                session.add(AgentSlot(id=str(uuid4()), slot_number=slot_num, is_available=True))
            session.commit()
            logger.info("Initialized %d shared execution slots", len(missing))
            return

        except IntegrityError:
            # silent-ok: expected create race; the retry loop re-reads the pool
            # Someone else created these between the read and the write. The
            # pool they built is the one this was going to build, so rolling
            # back and looking again is the whole recovery.
            session.rollback()
        except Exception:
            # Rolled back rather than left open: a failed commit poisons the
            # session, and every later query on it raises instead of
            # answering — which turned one lost race into a dead caller.
            session.rollback()
            logger.exception("Error initializing slots")
            return

    # Falling out of the loop means every attempt lost the race. That is
    # almost always benign — whoever won built the same pool — but it is not
    # provable from here, and returning quietly with no slots is how a queue
    # that never dispatches looks exactly like an idle one.
    logger.warning(
        "Slot pool initialization lost the create race on every attempt; "
        "assuming another worker built the pool"
    )


def _retire_surplus_slots(session: Session, size: int) -> None:
    """Delete idle slots numbered past the lane count. Commits if any went."""
    surplus = session.exec(
        select(AgentSlot).where(AgentSlot.slot_number > size).where(AgentSlot.is_available == True)  # noqa: E712
    ).all()
    for slot in surplus:
        session.delete(slot)
    if surplus:
        session.commit()
        logger.info(
            "Retired %d idle lane(s) past the lane count of %d: %s",
            len(surplus),
            size,
            sorted(slot.slot_number for slot in surplus),
        )
