"""Whether a ticket is *executing*, or merely open.

``tickets.state`` answers "has this work started and not finished" — it says
nothing about whether an agent is on it right now. Most of the in-progress pile
is idle: a ticket that ran once, stopped between stages, and now waits for
someone to press go. Reading ``in_progress`` as "running" overstates the board's
activity by an order of magnitude.

Activity is derived, never persisted: the answer changes the moment a run ends,
so a column would be stale by definition. It comes from the tables that record
execution — ``agent_runs``, ``orchestration_runs`` and ``queued_runs``. A child
row whose parent orchestration is already terminal is residue, not activity:
``status`` on those rows is a claim only the owner can retract, and an owner
that walked away left ``RUNNING`` behind. The queue already ignores them when
drawing busy lanes; the board must too.
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
#: A lane entry that currently holds a slot. ``STARTED`` is *not* here — that
#: is the terminal "lane released" state set by ``on_orchestration_complete``,
#: and treating it as live made finished (even blocked) tickets read "running".
_RUNNING_QUEUE_STATUSES = (QueuePosition.PROMOTED, QueuePosition.ACTIVE)
#: Waiting its turn behind whatever holds the slot.
_QUEUED_QUEUE_STATUSES = (QueuePosition.QUEUED, QueuePosition.SCHEDULED)
#: A parent orchestration still claiming a lane. QUEUED counts: a claim is a
#: promise that work is about to start, and a child bound to it is already
#: spoken for. Terminal statuses do not — leftover RUNNING children of a
#: failed/cancelled/succeeded orchestration are how the board used to stay at 1
#: after the queue had already gone idle.
_LIVE_ORCHESTRATION_STATUSES = (
    OrchestrationRunStatus.QUEUED,
    OrchestrationRunStatus.RUNNING,
)

#: Highest wins when a ticket has several signals at once — a ticket can hold a
#: slot *and* have a queued follow-up entry, and "running" is the honest label.
_PRECEDENCE = (
    TicketActivity.RUNNING,
    TicketActivity.AWAITING,
    TicketActivity.QUEUED,
    TicketActivity.IDLE,
)

#: States where "is it running" is a question worth asking at all.
#: PARKED is open but deliberately absent: these drive the activity axis
#: (running/awaiting/queued/idle), and a parked ticket is waiting on a person,
#: so it has no activity to classify (lg-workflow-integrity-449).
OPEN_STATES = (TicketState.BACKLOG, TicketState.IN_PROGRESS, TicketState.BLOCKED)


def _ids_with(session: Session, statement) -> set[str]:
    return {row for row in session.exec(statement).all() if row}


def _ids_tied_to_live_orchestration(
    session: Session,
    model: type[AgentRun] | type[QueuedRun],
    ids: set[str],
    statuses: tuple,
) -> set[str]:
    """Ticket ids with a row in ``statuses`` that is still backed by live work.

    Standalone rows (no ``orchestration_run_id``) stay: a manually started
    stage, or a lane reservation that has not bound an orchestration yet.
    Rows pointing at a terminal parent do not — that is leftover ``RUNNING``
    after the orchestration already failed.
    """
    standalone = _ids_with(
        session,
        select(model.ticket_id)
        .where(col(model.ticket_id).in_(ids))
        .where(col(model.status).in_(statuses))
        .where(col(model.orchestration_run_id).is_(None)),
    )
    under_live = _ids_with(
        session,
        select(model.ticket_id)
        .join(OrchestrationRun, model.orchestration_run_id == OrchestrationRun.id)
        .where(col(model.ticket_id).in_(ids))
        .where(col(model.status).in_(statuses))
        .where(col(OrchestrationRun.status).in_(_LIVE_ORCHESTRATION_STATUSES)),
    )
    return standalone | under_live


def classify_ticket_activity(
    session: Session, ticket_ids: Iterable[str]
) -> dict[str, TicketActivity]:
    """Map each requested ticket id to what is actually happening on it.

    Batched: a handful of selects for the whole set, not one per ticket. Ids
    with no execution rows come back as ``IDLE`` rather than missing, so
    callers never have to distinguish "not running" from "not asked about".
    """
    ids = {tid for tid in ticket_ids if tid}
    if not ids:
        return {}

    running = _ids_tied_to_live_orchestration(session, AgentRun, ids, _RUNNING_RUN_STATUSES)
    running |= _ids_with(
        session,
        select(OrchestrationRun.ticket_id)
        .where(col(OrchestrationRun.ticket_id).in_(ids))
        .where(OrchestrationRun.status == OrchestrationRunStatus.RUNNING),
    )
    running |= _ids_tied_to_live_orchestration(session, QueuedRun, ids, _RUNNING_QUEUE_STATUSES)

    awaiting = _ids_tied_to_live_orchestration(session, AgentRun, ids, _AWAITING_RUN_STATUSES)

    queued = _ids_tied_to_live_orchestration(session, QueuedRun, ids, _QUEUED_QUEUE_STATUSES)
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
        parked=states[TicketState.PARKED],
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
