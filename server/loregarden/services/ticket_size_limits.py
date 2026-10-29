"""How big a ticket may grow before the orchestrator splits it into parts.

Its own module so the read side (the ticket page saying why a ticket was split)
can name the limits without importing the split machinery, which reaches the
hierarchy service the tree is built by.
"""

from loregarden.models.domain import Ticket
from loregarden.services.acceptance_criteria import load_criteria

# Measured 2026-10-09: 40 of ~1,280 tickets exceed one or the other.
MAX_ACCEPTANCE_CRITERIA = 15
MAX_DESCRIPTION_CHARS = 15_000


def over_limits(ticket: Ticket) -> list[str]:
    """Each size limit the ticket is over, as a phrase a person reads."""
    reasons: list[str] = []
    criteria = len(load_criteria(ticket.acceptance_criteria_json))
    if criteria > MAX_ACCEPTANCE_CRITERIA:
        reasons.append(f"{criteria} criteria, over the {MAX_ACCEPTANCE_CRITERIA} limit")
    chars = len(ticket.description or "")
    if chars > MAX_DESCRIPTION_CHARS:
        reasons.append(f"{chars:,}-character description, over the {MAX_DESCRIPTION_CHARS:,} limit")
    return reasons


def is_oversized(ticket: Ticket) -> bool:
    return bool(over_limits(ticket))
