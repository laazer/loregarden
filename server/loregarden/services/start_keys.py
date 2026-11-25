"""Which run, or place in line, a start's idempotency key already names.

A key names one start of one ticket from the claim on: while it waits in a lane
it is on the queue entry, and from the moment a run is claimed for it, on that
run. It is released only when the claim fails before anything adopted it
(`release_unstarted`), because then no run was ever created for it to name.
"""

from __future__ import annotations

from loregarden.models.domain import (
    OrchestrationRun,
    OrchestrationRunStatus,
    QueuedRun,
    QueueEntryKind,
    QueuePosition,
)
from sqlmodel import Session, col, select

#: PAUSED is an entry held with its autopilot; releasing it puts it back in line.
_STILL_TO_START = (QueuePosition.QUEUED, QueuePosition.SCHEDULED, QueuePosition.PAUSED)


def run_for_key(session: Session, ticket_id: str, key: str) -> OrchestrationRun | None:
    if not key:
        return None
    return session.exec(
        select(OrchestrationRun).where(
            col(OrchestrationRun.ticket_id) == ticket_id,
            col(OrchestrationRun.idempotency_key) == key,
        )
    ).first()


def waiting_entry_for_key(session: Session, ticket_id: str, key: str) -> QueuedRun | None:
    """The lane entry a keyed start is still waiting in, before it has a run."""
    if not key:
        return None
    return session.exec(
        select(QueuedRun).where(
            col(QueuedRun.ticket_id) == ticket_id,
            col(QueuedRun.entry_kind) == QueueEntryKind.ORCHESTRATION,
            col(QueuedRun.status).in_(_STILL_TO_START),
            col(QueuedRun.idempotency_key) == key,
        )
    ).first()


def name_run(session: Session, run: OrchestrationRun, key: str) -> None:
    """Give an unnamed run a key no other run of its ticket holds. Not committed."""
    if key and not run.idempotency_key and run_for_key(session, run.ticket_id, key) is None:
        run.idempotency_key = key
        session.add(run)


def release_unstarted(run: OrchestrationRun) -> None:
    """Free the key of a claim that failed before anything adopted it.

    `started_at` is written by the adoption and by nothing earlier, so a run
    without one had no pipeline, no stage and no agent. From adoption on the
    start took effect (the ticket moved, agents may have run), and a retry must
    get that run. FAILED only: a cancelled or blocked claim was settled by
    somebody's decision, and a retry must not start what they stopped.
    """
    if run.started_at is None and run.status is OrchestrationRunStatus.FAILED:
        run.idempotency_key = ""
