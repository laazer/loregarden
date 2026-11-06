"""How big a ticket may grow before the orchestrator splits it into parts.

Its own module so the read side (the ticket page saying why a ticket was split)
can name the limits without importing the split machinery, which reaches the
hierarchy service the tree is built by.

Read-side only. The write-time caps and their guards live in
`services.ticket_growth_guard` and `services.acceptance_criteria`, which take
scalars and lists and import no ORM model — this module imports `Ticket`, so
calling it from a write seam would invert the dependency it exists to avoid.
"""

from loregarden.models.domain import Ticket
from loregarden.services.acceptance_criteria import load_criteria
from loregarden.services.ticket_growth_guard import MAX_DESCRIPTION_CHARS

#: When the orchestrator offers to split a ticket into parts. Named `SPLIT_AT_*`
#: rather than `MAX_*` because these are **not** the write-time caps: the
#: criteria one is 15 here and 12 at the write seam
#: (`acceptance_criteria.MAX_ACCEPTANCE_CRITERIA`), and two identically-spelled
#: constants holding different numbers is what the rename exists to stop.
#:
#: Do not lower this to the write cap. `is_oversized` is consulted at every
#: stage boundary and invokes a model that creates real child tickets; 29 live
#: tickets sat at 13-15 criteria on 2026-11-05, so unifying the two auto-splits
#: them on their next boundary and a revert does not delete the children.
#: Measured 2026-10-09: 40 of ~1,280 tickets exceed one or the other.
SPLIT_AT_ACCEPTANCE_CRITERIA = 15
#: The description trigger genuinely *is* the write cap, so it is imported
#: rather than respelled: one number, two names.
SPLIT_AT_DESCRIPTION_CHARS = MAX_DESCRIPTION_CHARS


def over_limits(ticket: Ticket) -> list[str]:
    """Each size limit the ticket is over, as a phrase a person reads."""
    reasons: list[str] = []
    criteria = len(load_criteria(ticket.acceptance_criteria_json))
    if criteria > SPLIT_AT_ACCEPTANCE_CRITERIA:
        reasons.append(f"{criteria} criteria, over the {SPLIT_AT_ACCEPTANCE_CRITERIA} limit")
    chars = len(ticket.description or "")
    if chars > SPLIT_AT_DESCRIPTION_CHARS:
        reasons.append(
            f"{chars:,}-character description, over the {SPLIT_AT_DESCRIPTION_CHARS:,} limit"
        )
    return reasons


def is_oversized(ticket: Ticket) -> bool:
    return bool(over_limits(ticket))
