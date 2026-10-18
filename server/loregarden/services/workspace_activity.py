"""What happened in a workspace since a given moment, one line per ticket.

The dashboard's "since you were away". The event log already holds the answer,
but not in a readable form. blobert logged 314 events in two weeks and
loregarden 6,578, most of them artifact writes and run starts, and a feed of
those is the wall of text CLAUDE.md warns about. So this keeps the events that
change what a person does next (`ActivityHighlightKind`), folds repeats into
counts, and groups them under the ticket they happened to.
"""

from __future__ import annotations

import json
import logging
from collections import Counter
from datetime import datetime

from loregarden.models.domain import (
    ActivityHighlightKind,
    DomainEvent,
    EventType,
    GateOutcome,
    RunStatus,
    StageCompletionOutcome,
    Ticket,
    TicketState,
    Workspace,
)
from pydantic import BaseModel, ConfigDict, ValidationError
from sqlmodel import Session, col, select

logger = logging.getLogger(__name__)

#: Tickets beyond this are counted in `more_tickets`, not listed.
TICKET_LIMIT = 50
#: Events read per call. A workspace busier than this inside the window gets a
#: digest of its newest events and `events_truncated`, never a silent cut.
EVENT_LIMIT = 20_000

_DIGESTED = (
    EventType.TICKET_CREATED,
    EventType.TICKET_STATE_CHANGED,
    EventType.TICKET_LANDED,
    EventType.STAGE_COMPLETED,
    EventType.AGENT_RUN_COMPLETED,
    EventType.GATE_EVALUATED,
    EventType.APPROVAL_REQUESTED,
)
_GATE_FAILURES = frozenset({GateOutcome.FAILED, GateOutcome.UNAVAILABLE})
_KIND_RANK = {kind: index for index, kind in enumerate(ActivityHighlightKind)}


class _Payload(BaseModel):
    """The payload fields the digest reads. Every event kind writes a subset."""

    model_config = ConfigDict(extra="ignore")

    state: TicketState | None = None
    ok: bool | None = None
    target: str = ""
    outcome: str = ""
    stage_key: str = ""
    status: RunStatus | None = None


class ActivityHighlight(BaseModel):
    kind: ActivityHighlightKind
    #: The state moved to, the landing target, or the stage; "" when none applies.
    detail: str = ""
    count: int


class TicketActivityDigest(BaseModel):
    ticket_id: str
    external_id: str
    title: str
    state: TicketState
    last_event_at: datetime
    highlights: list[ActivityHighlight]


class WorkspaceActivity(BaseModel):
    workspace_slug: str
    since: datetime
    #: The kinds the ticket list was narrowed to; empty means all.
    kinds: list[ActivityHighlightKind]
    #: Every highlight in the window, summed across tickets, for the headline.
    totals: dict[ActivityHighlightKind, int]
    #: Newest activity first, at most `TICKET_LIMIT`.
    tickets: list[TicketActivityDigest]
    more_tickets: int
    events_truncated: bool


def _highlight(event: DomainEvent) -> tuple[ActivityHighlightKind, str] | None:
    try:
        payload = _Payload.model_validate(json.loads(event.payload_json or "{}"))
    except (ValueError, ValidationError):
        # One unreadable event (an older build's payload, a retired enum value)
        # costs its own highlight, not the whole window.
        logger.warning("Unreadable payload on event %s (%s)", event.id, event.type, exc_info=True)
        return None
    match event.type:
        case EventType.TICKET_CREATED:
            return ActivityHighlightKind.CREATED, ""
        case EventType.TICKET_STATE_CHANGED if payload.state is not None:
            return ActivityHighlightKind.MOVED, payload.state.value
        case EventType.TICKET_LANDED if payload.ok is not None:
            kind = (
                ActivityHighlightKind.LANDED if payload.ok else ActivityHighlightKind.LANDING_FAILED
            )
            return kind, payload.target
        case EventType.STAGE_COMPLETED if payload.outcome == StageCompletionOutcome.REJECT:
            return ActivityHighlightKind.STAGE_REJECTED, payload.stage_key
        case EventType.AGENT_RUN_COMPLETED if payload.status is RunStatus.FAILED:
            return ActivityHighlightKind.RUN_FAILED, ""
        case EventType.GATE_EVALUATED if payload.outcome in _GATE_FAILURES:
            return ActivityHighlightKind.GATE_FAILED, payload.stage_key
        case EventType.APPROVAL_REQUESTED:
            return ActivityHighlightKind.APPROVAL_REQUESTED, payload.stage_key
    return None


def workspace_activity(
    session: Session,
    workspace: Workspace,
    since: datetime,
    kinds: frozenset[ActivityHighlightKind] = frozenset(),
) -> WorkspaceActivity:
    """The digest of ``workspace`` since ``since``.

    ``kinds`` narrows the ticket list to tickets with at least one highlight
    of those kinds (all of their highlights are still shown). `totals` always
    counts the whole window, so the filter chips can show what each one holds.
    """
    events = session.exec(
        select(DomainEvent)
        .where(
            col(DomainEvent.workspace_id) == workspace.id,
            col(DomainEvent.created_at) >= since,
            col(DomainEvent.type).in_(_DIGESTED),
            col(DomainEvent.ticket_id).is_not(None),
        )
        .order_by(col(DomainEvent.created_at).desc())
        .limit(EVENT_LIMIT)
    ).all()

    counts: dict[str, Counter[tuple[ActivityHighlightKind, str]]] = {}
    last_at: dict[str, datetime] = {}
    totals: Counter[ActivityHighlightKind] = Counter()
    for event in events:
        found = _highlight(event)
        if found is None or event.ticket_id is None:
            continue
        counts.setdefault(event.ticket_id, Counter())[found] += 1
        # Newest first, so the first event seen for a ticket is its latest.
        last_at.setdefault(event.ticket_id, event.created_at)
        totals[found[0]] += 1

    ordered = sorted(
        (
            ticket_id
            for ticket_id in last_at
            if not kinds or any(kind in kinds for kind, _ in counts[ticket_id])
        ),
        key=last_at.__getitem__,
        reverse=True,
    )
    shown = ordered[:TICKET_LIMIT]
    tickets = {t.id: t for t in session.exec(select(Ticket).where(col(Ticket.id).in_(shown))).all()}
    digests = [
        TicketActivityDigest(
            ticket_id=ticket_id,
            external_id=tickets[ticket_id].external_id,
            title=tickets[ticket_id].title,
            state=tickets[ticket_id].state,
            last_event_at=last_at[ticket_id],
            highlights=[
                ActivityHighlight(kind=kind, detail=detail, count=count)
                for (kind, detail), count in sorted(
                    counts[ticket_id].items(),
                    key=lambda item: (_KIND_RANK[item[0][0]], item[0][1]),
                )
            ],
        )
        for ticket_id in shown
        # A deleted ticket keeps its events; there is nothing left to link to.
        if ticket_id in tickets
    ]
    return WorkspaceActivity(
        workspace_slug=workspace.slug,
        since=since,
        kinds=sorted(kinds, key=_KIND_RANK.__getitem__),
        totals=dict(totals),
        tickets=digests,
        more_tickets=max(0, len(ordered) - TICKET_LIMIT),
        events_truncated=len(events) >= EVENT_LIMIT,
    )
