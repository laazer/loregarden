"""Work already waiting in a lane, and how a request for the same work is answered.

Retrying a refused request must not append a second entry for work already in
line. Three retries once produced three entries for one (ticket, stage), and
each would later promote and re-run that stage: one of them dispatched a real
agent against a stage that had been done for 26 minutes, and moved the workflow
cursor backwards onto it (lg-workflow-integrity-568).
"""

from __future__ import annotations

import logging

from loregarden.models.domain import QueuedRun, QueueEntryKind, QueuePosition
from sqlmodel import Session, select

logger = logging.getLogger(__name__)


def already_queued(
    session: Session, *, ticket_id: str, entry_kind: QueueEntryKind, stage_key: str
) -> QueuedRun | None:
    """A queued entry for this exact request, if one is already waiting.

    Keyed on (ticket, kind, stage) rather than ticket alone: "run this one
    stage" and "run everything left" are different asks, and two different
    stages of one ticket may legitimately both be waiting.
    """
    return session.exec(
        select(QueuedRun).where(
            QueuedRun.ticket_id == ticket_id,
            QueuedRun.status == QueuePosition.QUEUED,
            QueuedRun.entry_kind == entry_kind,
            QueuedRun.stage_key == stage_key,
        )
    ).first()


def place_in_line(entry: QueuedRun) -> str:
    return f"Already queued in slot {entry.slot_number} at position {entry.position}."


def reuse_entry(session: Session, entry: QueuedRun, *, idempotency_key: str) -> dict:
    """Answer a request with the entry already waiting for it."""
    logger.info(
        "Admission reusing queued entry %s for ticket %s stage %r",
        entry.id,
        entry.ticket_id,
        entry.stage_key,
    )
    if idempotency_key and not entry.idempotency_key:
        # The entry is this request's answer, so its key has to find it.
        entry.idempotency_key = idempotency_key
        session.add(entry)
        session.commit()
    return {
        "status": "queued",
        "slot_number": entry.slot_number,
        "entry_id": entry.id,
        "position": entry.position,
        "message": place_in_line(entry),
    }
