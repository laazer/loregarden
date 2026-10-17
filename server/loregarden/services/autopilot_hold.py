"""Turning an autopilot off holds the work it queued that has not started.

Off means "finish what is running, then stop". Not picking new work was only
half of that: a pick waits in a lane until the slot frees, and the lane starts
it then whether the autopilot is still on or not, so turning it off with three
tickets in line still ran all three. Off now holds them (PAUSED keeps the
entry's lane, and the lane passes over it) and on puts them back.

The entries are found through the autopilot's own log. A waiting entry is its
work when it logged a dispatch of that ticket no earlier than the entry was
made: the dispatch it queued, or a person's entry it found already in line and
reused. An entry a person queued after that is theirs, and is left alone. A
ticket queued through "start ready work" is logged the same way, and is held
with the rest. Each hold is logged as HELD, and turning on releases only the
tickets it held, so an entry a person paused by hand stays paused.
"""

from __future__ import annotations

import logging
from datetime import datetime

from loregarden.models.domain import (
    AutopilotAction,
    AutopilotEvent,
    InitiativePlan,
    QueuedRun,
    QueuePosition,
    Ticket,
    comparable_utc,
)
from loregarden.services.parallel_queue import WAITING_STATUSES
from loregarden.services.queue_lanes import QueueLaneService
from loregarden.services.ticket_state_service import RESOLVED_STATES
from loregarden.websocket_events import emit_execution_update
from sqlalchemy import func
from sqlmodel import Session, col, select

logger = logging.getLogger(__name__)


def _held_since(session: Session, plan_id: str, since: datetime | None) -> set[str]:
    query = select(AutopilotEvent.ticket_id).where(
        AutopilotEvent.initiative_id == plan_id, AutopilotEvent.action == AutopilotAction.HELD
    )
    if since is not None:
        query = query.where(AutopilotEvent.created_at >= since)
    return {tid for tid in session.exec(query).all() if tid}


def _last_dispatches(session: Session, plan_id: str) -> dict[str, datetime]:
    """Ticket id -> when it last dispatched it."""
    rows = session.exec(
        select(AutopilotEvent.ticket_id, func.max(AutopilotEvent.created_at))
        .where(
            AutopilotEvent.initiative_id == plan_id,
            AutopilotEvent.action == AutopilotAction.DISPATCHED,
        )
        .group_by(AutopilotEvent.ticket_id)
    ).all()
    return {tid: comparable_utc(at) for tid, at in rows if tid and at}


def _lane_entries(
    session: Session, ticket_ids: set[str], statuses: tuple[QueuePosition, ...]
) -> list[QueuedRun]:
    if not ticket_ids:
        return []
    return list(
        session.exec(
            select(QueuedRun).where(
                col(QueuedRun.ticket_id).in_(ticket_ids),
                col(QueuedRun.status).in_(statuses),
                # Lane entries have no run until they start; a shared-queue
                # entry does, and is not the autopilot's.
                col(QueuedRun.run_id).is_(None),
            )
        ).all()
    )


def _hold(lanes: QueueLaneService, entry: QueuedRun) -> bool:
    """Keep a waiting entry in its lane without letting the lane start it.

    PAUSED is outside `WAITING_STATUSES`, so `start_lane_head` passes over it;
    the lane closes up behind it. False when the entry is not waiting.
    """
    if entry.status not in WAITING_STATUSES:
        return False
    entry.status = QueuePosition.PAUSED
    lanes.session.add(entry)
    lanes.session.commit()
    lanes.renumber(entry.slot_number)
    return True


def _release(lanes: QueueLaneService, entry: QueuedRun) -> bool:
    """Put a held entry back at the end of its lane, and start it if the lane is free.

    The end, not its old place: what queued behind it while it was held has been
    waiting its turn since. False when the entry is not held.
    """
    if entry.status != QueuePosition.PAUSED:
        return False
    # Counted before the status flips, or autoflush counts the entry itself.
    entry.position = len(lanes.waiting_in_lane(entry.slot_number)) + 1
    entry.status = QueuePosition.QUEUED
    lanes.session.add(entry)
    lanes.session.commit()
    lanes.start_lane_head(entry.slot_number)
    return True


def hold_queued_work(session: Session, plan: InitiativePlan) -> list[str]:
    """Hold every unstarted lane entry it queued.

    Returns the held tickets' ids. The caller has already turned it off.
    """
    last_dispatch = _last_dispatches(session, plan.initiative_id)
    lanes = QueueLaneService(session)
    held: list[str] = []
    for entry in _lane_entries(session, set(last_dispatch), WAITING_STATUSES):
        if comparable_utc(entry.created_at) > last_dispatch[entry.ticket_id]:
            continue
        slot = entry.slot_number
        if _hold(lanes, entry):
            held.append(entry.ticket_id)
            session.add(
                AutopilotEvent(
                    initiative_id=plan.initiative_id,
                    action=AutopilotAction.HELD,
                    ticket_id=entry.ticket_id,
                    detail=f"held in agent slot {slot} until it is turned back on",
                )
            )
    if held:
        session.commit()
        emit_execution_update()
    return held


def release_held_work(session: Session, plan_id: str, held_since: datetime | None) -> list[str]:
    """Put back what turning it off held, now it is on again.

    `held_since` is when it was last turned on: every hold since then is one this
    on-period's turn-off made, and anything older was released already. A ticket
    that finished while held, or that someone queued again, is not run twice:
    its held entry is cancelled. Returns the released tickets' ids.
    """
    held = _held_since(session, plan_id, held_since)
    lanes = QueueLaneService(session)
    released: list[str] = []
    for entry in _lane_entries(session, held, (QueuePosition.PAUSED,)):
        ticket = session.get(Ticket, entry.ticket_id)
        requeued = lanes.already_queued(
            ticket_id=entry.ticket_id, entry_kind=entry.entry_kind, stage_key=entry.stage_key
        )
        if ticket is None or ticket.state in RESOLVED_STATES or requeued is not None:
            entry.status = QueuePosition.CANCELLED
            session.add(entry)
            reason = "finished while held" if requeued is None else "queued again while held"
            logger.info(
                "Autopilot %s: dropped held entry %s for %s (%s)",
                plan_id,
                entry.id,
                entry.ticket_id,
                reason,
            )
            detail = f"not released: {reason}"
        elif _release(lanes, entry):
            released.append(entry.ticket_id)
            detail = f"back in agent slot {entry.slot_number}"
        else:
            continue
        session.add(
            AutopilotEvent(
                initiative_id=plan_id,
                action=AutopilotAction.RELEASED,
                ticket_id=entry.ticket_id,
                detail=detail,
            )
        )
    session.commit()
    if held:
        emit_execution_update()
    return released
