"""Whether the orchestrator split a ticket into parts, and why, for the ticket page.

The split is recorded as a `split_oversized_ticket` orchestrator decision, the
same event the history rail reads. The page needs it as a fact about the ticket,
not one row among the history's: a split ticket stops running stages of its own,
and until it says so it reads like any other ticket.
"""

from __future__ import annotations

import json

from loregarden.models.domain import (
    DomainEvent,
    EventType,
    OrchestratorDecision,
    Ticket,
    TicketSplit,
)
from loregarden.services.ticket_size_limits import over_limits
from sqlalchemy import func
from sqlmodel import Session, col, select


def split_ticket_ids(session: Session, ticket_ids: list[str]) -> set[str]:
    """Which of these tickets the orchestrator split, in one query."""
    if not ticket_ids:
        return set()
    rows = session.exec(
        select(DomainEvent.ticket_id)
        .where(col(DomainEvent.ticket_id).in_(ticket_ids))
        .where(DomainEvent.type == EventType.ORCHESTRATOR_DECISION)
        .where(
            func.json_extract(DomainEvent.payload_json, "$.decision")
            == OrchestratorDecision.SPLIT_OVERSIZED_TICKET.value
        )
        .distinct()
    ).all()
    return {ticket_id for ticket_id in rows if ticket_id}


def ticket_split(session: Session, ticket: Ticket) -> TicketSplit | None:
    """The ticket's latest split, or None when the orchestrator never split it."""
    event = session.exec(
        select(DomainEvent)
        .where(DomainEvent.ticket_id == ticket.id)
        .where(DomainEvent.type == EventType.ORCHESTRATOR_DECISION)
        .where(
            func.json_extract(DomainEvent.payload_json, "$.decision")
            == OrchestratorDecision.SPLIT_OVERSIZED_TICKET.value
        )
        .order_by(col(DomainEvent.created_at).desc())
        .limit(1)
    ).first()
    if event is None:
        return None
    payload = json.loads(event.payload_json)
    return TicketSplit(
        stage_key=payload["stage_key"],
        split_at=event.created_at,
        over_limits=over_limits(ticket),
    )
