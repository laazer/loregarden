"""Ticket relations: symmetric, non-blocking "see also" links.

Unlike ``ticket_dependencies`` these carry no direction and no ordering — they do
not steer subtree run order and nothing consults them before dispatch. They exist
so a reader of one ticket finds the others sharing its context.

Because the link is symmetric, a pair is stored exactly once, in canonical id
order (``min`` first). Every read has to check both columns; every write has to
canonicalize first. Both live here so no caller has to remember either rule.
"""

from __future__ import annotations

from loregarden.models.domain import TicketRelation
from sqlmodel import Session, or_, select


def _pair(ticket_id: str, other_id: str) -> tuple[str, str]:
    """The (low, high) canonical storage order for a relation between two tickets."""
    return (ticket_id, other_id) if ticket_id <= other_id else (other_id, ticket_id)


class TicketRelationService:
    def __init__(self, session: Session) -> None:
        self.session = session

    def add_relation(
        self, ticket_id: str, related_ticket_id: str, *, created_by: str = ""
    ) -> TicketRelation:
        """Relate two tickets. Idempotent in both directions; rejects self-links.

        No cycle check, unlike dependencies — a relation graph has no ordering to
        contradict, so any shape of it is valid.
        """
        if ticket_id == related_ticket_id:
            raise ValueError("A ticket cannot be related to itself")
        low, high = _pair(ticket_id, related_ticket_id)
        existing = self._find(low, high)
        if existing:
            return existing
        edge = TicketRelation(
            ticket_id=low,
            related_ticket_id=high,
            created_by=created_by,
        )
        self.session.add(edge)
        self.session.commit()
        self.session.refresh(edge)
        return edge

    def remove_relation(self, ticket_id: str, related_ticket_id: str) -> bool:
        low, high = _pair(ticket_id, related_ticket_id)
        edge = self._find(low, high)
        if not edge:
            return False
        self.session.delete(edge)
        self.session.commit()
        return True

    def related(self, ticket_id: str) -> list[str]:
        """Ids of every ticket related to this one, from either end of the pair."""
        rows = self.session.exec(
            select(TicketRelation).where(
                or_(
                    TicketRelation.ticket_id == ticket_id,
                    TicketRelation.related_ticket_id == ticket_id,
                )
            )
        ).all()
        return [
            row.related_ticket_id if row.ticket_id == ticket_id else row.ticket_id for row in rows
        ]

    def _find(self, low: str, high: str) -> TicketRelation | None:
        return self.session.exec(
            select(TicketRelation).where(
                TicketRelation.ticket_id == low,
                TicketRelation.related_ticket_id == high,
            )
        ).first()
