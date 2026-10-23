"""Who holds a slot, and when its claim may be taken.

One question, asked from three places: `queue_lanes` releasing the lane an
entry names, `parallel_queue` sweeping lanes whose occupant is gone, and the
`every_busy_lane_has_an_occupant` invariant's detector. It used to be written
out by hand in the first two, and they disagreed — see `claim_is_fresh` for the
input they disagreed on and the direction this resolves it in (775).

Pure predicates over an `AgentSlot`: no session, no clock of their own. The
callers hand in the window they mean, because they do not mean the same one —
the sweep asks "was this claimed too recently to collect?" and the lane asks
"is this claim someone else's?", and a predicate that answered both at once
would give each caller the other's window.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from loregarden.core.timestamps import as_utc
from loregarden.models.domain import AgentSlot

#: How long a slot claimed by a reservation may name nothing before a sweep
#: treats it as residue. Long enough to cover reserve-to-bind, which is a few
#: statements on the same thread; short enough that a caller which dies between
#: them costs one lane for one minute rather than until restart.
RESERVATION_GRACE = timedelta(seconds=60)


def slot_is_busy_with_nothing(slot: AgentSlot) -> bool:
    """Whether the slot reads occupied while naming no occupant at all.

    The bad state 775 is about, and not the ordinary residue beside it: a slot
    still naming a run that finished is swept by occupant liveness, while a
    slot naming *nothing* is one nothing can ever release. Occupancy is either
    id, because a lane is held by an orchestration and a single dispatched
    stage by an agent run; an id written as an empty string is no occupant,
    which is how both original readers already read it.
    """
    if slot.is_available:
        return False
    return not (slot.current_orchestration_run_id or slot.current_run_id)


def claim_is_fresh(
    slot: AgentSlot,
    *,
    now: datetime | None = None,
    not_claimed_after: datetime | None = None,
) -> bool:
    """Whether this claim must be left alone. True means do not take the slot.

    Two independent reasons to leave it, and either is enough:

    - `now` — the claim is inside `RESERVATION_GRACE`. `claim_free_slot` takes a
      row with both ids null so two requests cannot read it as free, and the
      caller fills them in on `bind`; a sweep landing in that window would let a
      second orchestration into the lane. Only the sweep passes this: the lane's
      own release must not inherit the grace, or a lane added to and completed
      within the minute would never be released.
    - `not_claimed_after` — the claim postdates that instant, so it belongs to
      something later than whoever is asking (the entry's `promoted_at`).

    **A claim with no `assigned_at` is treated as fresh**, which is the stricter
    of the two answers the hand-written copies gave. `queue_lanes._claimed_after`
    said "not a later claim" and released it; `parallel_queue`'s grace test said
    "not recent" and reclaimed it. Both took the slot, and a claim we cannot
    date is a claim we cannot prove is dead — so it is left in place. Every
    claim writer stamps `assigned_at`, so the only rows this reaches are ones
    written by hand, and those are exactly the ones not to pull a lane out from
    under (775, AC6).

    Both comparisons coerce through `core.timestamps.as_utc`: SQLite hands back
    naive UTC, and an offset-aware value has to be *converted* rather than
    relabelled or a claim made an hour ago at +09:00 reads as hours in the
    future and the lane becomes unreclaimable.
    """
    assigned_at = slot.assigned_at
    if assigned_at is None:
        return True
    claimed = as_utc(assigned_at)
    if now is not None and as_utc(now) - claimed < RESERVATION_GRACE:
        return True
    return not_claimed_after is not None and claimed > as_utc(not_claimed_after)
