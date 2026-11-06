"""Vocabulary for what a landing ticket is waiting on.

Split out of `enums` (near its size cap), the same way `merge_queue_enums` was.
"""

from __future__ import annotations

from enum import StrEnum


class LandingPhase(StrEnum):
    """Where a ticket at its terminal stage is between "the last stage passed"
    and "done": what is holding it, in the order a landing meets them."""

    #: No landing in flight: no run on the terminal stage and no land lease.
    NOT_LANDING = "not_landing"
    #: The landing run is merging, publishing or recording; no tests are held.
    MERGING = "merging"
    #: The land verification is in the capacity line, behind other work.
    WAITING_FOR_CAPACITY = "waiting_for_capacity"
    #: The land verification holds capacity and its tests are running.
    VERIFYING = "verifying"
