"""Whether a ticket is *executing*, or merely open.

``tickets.state`` answers "has this work started and not finished" — it says
nothing about whether an agent is on it right now. Most of the in-progress pile
is idle: a ticket that ran once, stopped between stages, and now waits for
someone to press go. Reading ``in_progress`` as "running" overstates the board's
activity by an order of magnitude.

Activity is derived, never persisted: the answer changes the moment a run ends,
so a column would be stale by definition. It comes from the tables that record
execution — ``agent_runs``, ``orchestration_runs`` and ``queued_runs``.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable

from loregarden.models.domain import (
    AgentRun,
    OrchestrationRun,
    OrchestrationRunStatus,
    QueuedRun,
    QueuePosition,
    RunStatus,
    Ticket,
    TicketActivity,
    TicketState,
    TicketStatusSummary,
)
from sqlmodel import Session, col, select

#: An agent process exists for the ticket.
_RUNNING_RUN_STATUSES = (RunStatus.RUNNING,)
#: The process exists but is parked on an approval — running, but not advancing.
_AWAITING_RUN_STATUSES = (RunStatus.AWAITING_PERMISSION,)
#: A lane entry that has been handed a slot; the dispatch is under way.
_RUNNING_QUEUE_STATUSES = (QueuePosition.PROMOTED, QueuePosition.STARTED, QueuePosition.ACTIVE)
#: Waiting its turn behind whatever holds the slot.
_QUEUED_QUEUE_STATUSES = (QueuePosition.QUEUED, QueuePosition.SCHEDULED)

#: Highest wins when a ticket has several signals at once — a ticket can hold a
#: slot *and* have a queued follow-up entry, and "running" is the honest label.
_PRECEDENCE = (
    TicketActivity.RUNNING,
    TicketActivity.AWAITING,
    TicketActivity.QUEUED,
    TicketActivity.IDLE,
)

#: States where "is it running" is a question worth asking at all.
OPEN_STATES = (TicketState.BACKLOG, TicketState.IN_PROGRESS, TicketState.BLOCKED)


def _ids_with(session: Session, statement) -> set[str]:
    return {row for row in session.exec(statement).all() if row}


def classify_ticket_activity(
    session: Session, ticket_ids: Iterable[str]
) -> dict[str, TicketActivity]:
    """Map each requested ticket id to what is actually happening on it.

    Batched: four selects for the whole set, not four per ticket. Ids with no
    execution rows come back as ``IDLE`` rather than missing, so callers never
    have to distinguish "not running" from "not asked about".
    """
    ids = {tid for tid in ticket_ids if tid}
    if not ids:
        return {}

    running = _ids_with(
        session,
        select(AgentRun.ticket_id)
        .where(col(AgentRun.ticket_id).in_(ids))
        .where(col(AgentRun.status).in_(_RUNNING_RUN_STATUSES)),
    )
    running |= _ids_with(
        session,
        select(OrchestrationRun.ticket_id)
        .where(col(OrchestrationRun.ticket_id).in_(ids))
        .where(OrchestrationRun.status == OrchestrationRunStatus.RUNNING),
    )
    running |= _ids_with(
        session,
        select(QueuedRun.ticket_id)
        .where(col(QueuedRun.ticket_id).in_(ids))
        .where(col(QueuedRun.status).in_(_RUNNING_QUEUE_STATUSES)),
    )

    awaiting = _ids_with(
        session,
        select(AgentRun.ticket_id)
        .where(col(AgentRun.ticket_id).in_(ids))
        .where(col(AgentRun.status).in_(_AWAITING_RUN_STATUSES)),
    )

    queued = _ids_with(
        session,
        select(QueuedRun.ticket_id)
        .where(col(QueuedRun.ticket_id).in_(ids))
        .where(col(QueuedRun.status).in_(_QUEUED_QUEUE_STATUSES)),
    )
    queued |= _ids_with(
        session,
        select(OrchestrationRun.ticket_id)
        .where(col(OrchestrationRun.ticket_id).in_(ids))
        .where(OrchestrationRun.status == OrchestrationRunStatus.QUEUED),
    )

    by_activity = {
        TicketActivity.RUNNING: running,
        TicketActivity.AWAITING: awaiting,
        TicketActivity.QUEUED: queued,
    }
    result: dict[str, TicketActivity] = {}
    for ticket_id in ids:
        result[ticket_id] = next(
            (a for a in _PRECEDENCE if ticket_id in by_activity.get(a, ())),
            TicketActivity.IDLE,
        )
    return result


def activity_for(session: Session, ticket_id: str) -> TicketActivity:
    """Single-ticket convenience for the detail endpoints."""
    return classify_ticket_activity(session, [ticket_id]).get(ticket_id, TicketActivity.IDLE)


def summarize_ticket_status(
    session: Session, *, workspace_id: str | None = None
) -> TicketStatusSummary:
    """Counts by state, plus how much of that pile is actually moving.

    ``running``/``awaiting``/``queued`` are counted across every open ticket, not
    only the in-progress ones: a ticket can be dispatched from the backlog, and a
    board that hid those would under-report what the machine is doing. ``idle``
    is deliberately narrower — in-progress tickets with nothing behind them, the
    number this whole module exists to surface.
    """
    query = select(Ticket)
    if workspace_id:
        query = query.where(Ticket.workspace_id == workspace_id)
    tickets = list(session.exec(query).all())

    states = Counter(t.state for t in tickets)
    open_tickets = [t for t in tickets if t.state in OPEN_STATES]
    activity = classify_ticket_activity(session, [t.id for t in open_tickets])
    counts = Counter(activity.values())

    return TicketStatusSummary(
        backlog=states[TicketState.BACKLOG],
        in_progress=states[TicketState.IN_PROGRESS],
        blocked=states[TicketState.BLOCKED],
        done=states[TicketState.DONE],
        wont_do=states[TicketState.WONT_DO],
        running=counts[TicketActivity.RUNNING],
        awaiting=counts[TicketActivity.AWAITING],
        queued=counts[TicketActivity.QUEUED],
        idle=sum(
            1
            for t in open_tickets
            if t.state == TicketState.IN_PROGRESS
            and activity.get(t.id, TicketActivity.IDLE) == TicketActivity.IDLE
        ),
    )
