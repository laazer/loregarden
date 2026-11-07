"""The digest of what the control plane decided without a person, across a ticket tree.

A milestone that runs unattended makes three decisions a person would have made
before: it splits criteria a ticket could not meet into a new ticket, it
defers findings a spec would not decide, and it signs a ticket's gate on its
criteria check. Each one is on its ticket's history,
but nobody reads the history of every ticket under a milestone, so this lists
them for the whole tree in one place, newest first, for a person to review
after the fact.
"""

from __future__ import annotations

import json
from datetime import datetime

from loregarden.models.domain import (
    DomainEvent,
    EventType,
    OrchestratorDecision,
    Ticket,
)
from loregarden.services.hierarchy_service import descendants_by_root
from pydantic import BaseModel
from sqlmodel import Session, col, select

#: The decisions that stand in for a person's. Routine ones (a ticket sent back
#: to verify, criteria checked) are on the history and not worth a review.
REVIEWABLE = (
    OrchestratorDecision.SPLIT_CRITERIA,
    OrchestratorDecision.DEFERRED_UNDECIDED_FINDINGS,
    OrchestratorDecision.SIGNED_OFF_ON_CRITERIA,
)
_REVIEWABLE_VALUES = frozenset(d.value for d in REVIEWABLE)

#: Enough for the largest tree's history; the tree's newest decisions first.
DIGEST_LIMIT = 200


class AutoDecisionView(BaseModel):
    ticket_id: str
    external_id: str
    title: str
    decision: OrchestratorDecision
    reason: str
    at: datetime
    #: The ticket the decision produced or names (a split ticket), when it has one.
    related_external_id: str = ""


def auto_decisions_under(session: Session, root: Ticket) -> list[AutoDecisionView]:
    """The reviewable decisions on ``root`` and every ticket under it, newest first."""
    tree = {root.id: root, **{t.id: t for t in descendants_by_root(session, [root.id])[root.id]}}
    events = session.exec(
        select(DomainEvent)
        .where(DomainEvent.type == EventType.ORCHESTRATOR_DECISION)
        .where(col(DomainEvent.ticket_id).in_(list(tree)))
        .order_by(col(DomainEvent.created_at).desc())
    ).all()
    views: list[AutoDecisionView] = []
    for event in events:
        payload = json.loads(event.payload_json or "{}")
        if payload.get("decision") not in _REVIEWABLE_VALUES:
            continue
        ticket = tree[event.ticket_id or ""]
        views.append(
            AutoDecisionView(
                ticket_id=ticket.id,
                external_id=ticket.external_id,
                title=ticket.title,
                decision=OrchestratorDecision(payload["decision"]),
                reason=payload.get("reason") or "",
                at=event.created_at,
                # `record_orchestrator_decision` merges its evidence into the payload.
                related_external_id=payload.get("split_ticket") or "",
            )
        )
        if len(views) >= DIGEST_LIMIT:
            break
    return views
