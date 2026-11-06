"""The one error every capacity wait raises when the line stops moving.

A run, a stage or an orchestration that could not get its lease did no work: the
machine was full and nothing ahead finished for the stall window. Filed as WORK,
that sent the operator looking for a defect in code nobody had examined
(lg-milestone-that-1431, 2026-11-06), the same misfiling lg-machine-resource-2112
fixed for gates. So the message carries a fixed prefix the block classifier reads
as HARNESS, and the run boundary declares HARNESS from the type.

Imports nothing, so `block_classification` can read the prefix without a cycle.
"""

from __future__ import annotations

#: Opens every capacity-unavailable message; `block_classification` reads it as HARNESS.
CAPACITY_UNAVAILABLE_PREFIX = "Out of machine capacity:"


class CapacityUnavailable(RuntimeError):
    """A wait for capacity ended because the line stopped moving.

    Raised so the run fails, never runs without its lease, and names who held the
    line. A long wait in a moving line is not this.
    """

    def __init__(self, detail: str) -> None:
        super().__init__(f"{CAPACITY_UNAVAILABLE_PREFIX} {detail}")
