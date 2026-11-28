"""A lane hold that ends without its repair leaves the ticket blocked, as it was.

`hold_for_repair` re-arms the blocked stage at once (PENDING), so the ticket
reads in progress for the length of the hold. When the hold ends as the block
it always was — the attempt or wall-clock cap spent, the lane reclaimed, the
route gone — `QueueLaneService._settle_repair` released the lane and left the
stage re-armed. Nothing was going to run it: the ticket read in progress with
no lane, no run and no queue entry, and stayed so until a person noticed
(lg-milestone-that-1420, three tickets live on 2026-11-14).

The kind of block is recorded when the hold begins (`QueuedRun.held_block_kind`),
because re-arming clears `Ticket.block_kind`; a hold recorded before that column
existed falls back to classifying the block message.
"""

from __future__ import annotations

from datetime import datetime

from loregarden.models.domain import (
    BlockKind,
    OrchestrationRun,
    OrchestrationRunStatus,
    QueuedRun,
    QueuePosition,
    StageStatus,
    Ticket,
    TicketState,
)
from loregarden.services.block_classification import classify_block_message
from loregarden.services.parallel_queue import tickets_holding_lanes
from loregarden.services.requeue import requeued_since
from loregarden.services.ticket_rollup import reconcile_ancestors
from loregarden.services.workflow_service import resolve_ticket_stages, workflow_instance_for
from loregarden.services.workflow_state import parse_stage_map, set_stage_status
from sqlmodel import Session, col, select

#: Entries that still stand for live or upcoming work on their ticket. A ticket
#: with one of these is not stranded, whatever its stage map says.
_LIVE_ENTRY = (
    QueuePosition.QUEUED,
    QueuePosition.SCHEDULED,
    QueuePosition.PROMOTED,
    QueuePosition.ACTIVE,
    QueuePosition.PAUSED,
    QueuePosition.REPAIRING,
)
_LIVE_ORCHESTRATION = (OrchestrationRunStatus.QUEUED, OrchestrationRunStatus.RUNNING)


def _cursor_is_rearmed(session: Session, ticket: Ticket) -> bool:
    """True when the ticket reads in progress only because its stage is PENDING."""
    if ticket.state != TicketState.IN_PROGRESS:
        return False
    instance = workflow_instance_for(session, ticket.id)
    if instance is None:
        return False
    _, stages = resolve_ticket_stages(session, ticket)
    if not stages:
        return False
    stage_map = parse_stage_map(instance, stages)
    return stage_map.get(ticket.workflow_stage_key) == StageStatus.PENDING


def _has_live_work(session: Session, ticket: Ticket, *, ignoring: str | None) -> bool:
    """Whether anything still runs or waits to run this ticket."""
    if ticket.id in tickets_holding_lanes(session):
        return True
    live_entry = session.exec(
        select(QueuedRun.id)
        .where(QueuedRun.ticket_id == ticket.id)
        .where(col(QueuedRun.status).in_(_LIVE_ENTRY))
        .where(col(QueuedRun.id) != (ignoring or ""))
    ).first()
    if live_entry is not None:
        return True
    return (
        session.exec(
            select(OrchestrationRun.id)
            .where(OrchestrationRun.ticket_id == ticket.id)
            .where(col(OrchestrationRun.status).in_(_LIVE_ORCHESTRATION))
        ).first()
        is not None
    )


def _block_kind(entry: QueuedRun, ticket: Ticket) -> BlockKind:
    if entry.held_block_kind is not None:
        return entry.held_block_kind
    return classify_block_message(entry.failure_reason or ticket.blocking_issues)


def reblock_settled(session: Session, entry: QueuedRun, ticket: Ticket) -> bool:
    """Put a settled hold's re-armed stage back to BLOCKED; whether it did.

    Left alone: a ticket that moved on (done, parked, blocked already, a stage
    past the one held), and one that something still runs or waits to run —
    the hold that ends because the ticket restarted in another lane must not
    block the run that replaced it. Call it after the lane is released.
    """
    if not _cursor_is_rearmed(session, ticket):
        return False
    if _has_live_work(session, ticket, ignoring=entry.id):
        return False
    instance = workflow_instance_for(session, ticket.id)
    _, stages = resolve_ticket_stages(session, ticket)
    if instance is None:
        return False
    set_stage_status(ticket, instance, stages, ticket.workflow_stage_key, StageStatus.BLOCKED)
    ticket.block_kind = _block_kind(entry, ticket)
    if not ticket.blocking_issues and entry.failure_reason:
        ticket.blocking_issues = entry.failure_reason
    session.add(instance)
    session.add(ticket)
    session.commit()
    reconcile_ancestors(session, ticket)
    return True


def _latest_entries(session: Session) -> dict[str, QueuedRun]:
    """Each ticket's newest queue entry: the one that says what happened last."""
    latest: dict[str, QueuedRun] = {}
    for entry in session.exec(select(QueuedRun).order_by(col(QueuedRun.created_at))).all():
        latest[entry.ticket_id] = entry
    return latest


def settled_holds_left_rearmed(session: Session) -> list[tuple[QueuedRun, Ticket]]:
    """Tickets whose last queue entry was a hold that settled, still reading in progress.

    A settled hold is a STARTED entry with a repair route and a failure on it:
    `_settle_repair` writes exactly that. A hold whose repair worked ends ACTIVE
    and completes like any run, so it is not one of these.
    """
    found: list[tuple[QueuedRun, Ticket]] = []
    for entry in _latest_entries(session).values():
        if entry.status != QueuePosition.STARTED or entry.repair_route is None:
            continue
        if not entry.failure_reason:
            continue
        ticket = session.get(Ticket, entry.ticket_id)
        if ticket is None or not _cursor_is_rearmed(session, ticket):
            continue
        if _has_live_work(session, ticket, ignoring=entry.id):
            continue
        # A person requeued the stage after this hold: the PENDING stage is their
        # decision, not the hold's leftover re-arm. Re-blocking it undid
        # lg-milestone-that-1424's requeue 12s after it was made (2026-11-28).
        if requeued_since(session, ticket, ticket.workflow_stage_key, _settled_at(entry)):
            continue
        found.append((entry, ticket))
    return found


def _settled_at(entry: QueuedRun) -> datetime:
    """The latest moment this hold is known to have acted on the ticket."""
    return max(
        moment
        for moment in (
            entry.created_at,
            entry.started_at,
            entry.repairing_since,
            entry.last_failed_at,
        )
        if moment is not None
    )
