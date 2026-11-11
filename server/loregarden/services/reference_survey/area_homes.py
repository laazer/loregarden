"""External ids of project areas' home tickets, for display.

Kept apart from `areas` so that module never names `Ticket`: it writes an
area's `.description`, which is not a ticket's, and the ticket-content census
(`test_ticket_size_guard`) treats any Ticket-aware module's `.description`
assignment as a ticket write.
"""

from __future__ import annotations

from loregarden.models.domain import Ticket
from sqlmodel import Session, col, select


def home_external_ids(session: Session, ticket_ids: list[str]) -> dict[str, str]:
    """Ticket id -> external id, for the given ids that exist."""
    if not ticket_ids:
        return {}
    rows = session.exec(
        select(Ticket.id, Ticket.external_id).where(col(Ticket.id).in_(ticket_ids))
    ).all()
    return dict(rows)
