"""Whether a ticket is deferred, apart from everything that acts on it.

Readiness (`dependency_readiness`, `ticket_dependencies`, the plan graph) asks
this on every check, and `ticket_deferral` imports those modules, so the
predicate lives where nothing it serves has to import back.
"""

from __future__ import annotations

from loregarden.models.domain import Ticket
from loregarden.services.ticket_state_service import RESOLVED_STATES


def is_deferred(ticket: Ticket) -> bool:
    """Deferred and not yet resolved: it holds no dependent, and no parent reaches done past it.

    A deferral ends when the ticket is resolved; nothing has to clear the
    column for a done ticket to stop counting.
    """
    return ticket.deferred_at is not None and ticket.state not in RESOLVED_STATES
