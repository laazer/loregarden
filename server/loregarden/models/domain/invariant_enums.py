"""What a state invariant's re-entry means (`services.invariants`)."""

from __future__ import annotations

from enum import StrEnum

__all__ = ["InvariantReentry"]


class InvariantReentry(StrEnum):
    """Whether finding the bad state again is a defect or the world moving.

    The two used to look identical: a sweep repaired the row and returned. A
    sweep that fires forty times a week because a writer skips its guard reads,
    from outside, exactly like one that fires because people merge PRs by hand.
    """

    #: A writer is supposed to make this state unreachable, and a test claims it
    #: does. Every violation that entered after the guard landed falsifies that
    #: claim, so it is reported as a monitor finding and escalated to a bug.
    DEFECT = "defect"
    #: The state is entered legitimately from outside the control plane (a PR
    #: merged on GitHub, a conflict resolved by hand). Repair is the mechanism,
    #: so violations are counted and repaired, not escalated.
    EXPECTED = "expected"
