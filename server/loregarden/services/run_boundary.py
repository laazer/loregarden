"""How the orchestrator blocks a ticket on an exception that reached its run boundary.

Dispatched on the exception's type. A capacity failure means no agent ran, so the
block is the machine's (HARNESS), declared because the classifier's fallback for
a stage pointer is WORK (lg-milestone-that-1431). Anything else is the
orchestrator itself stopping, which no repair turn inside it can fix.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import singledispatch

from loregarden.models.domain import BlockKind
from loregarden.services.block_settlement import RepairPolicy
from loregarden.services.capacity_errors import CapacityUnavailable


@dataclass(frozen=True)
class BoundaryBlock:
    repair: RepairPolicy
    #: None leaves the kind to the message classifier.
    declared: BlockKind | None


@singledispatch
def boundary_block(exc: Exception) -> BoundaryBlock:
    return BoundaryBlock(
        RepairPolicy.withheld(
            f"The orchestrator itself stopped with {type(exc).__name__}; a repair "
            "turn runs inside the orchestrator, so this one is for a person."
        ),
        None,
    )


@boundary_block.register
def _(exc: CapacityUnavailable) -> BoundaryBlock:
    return BoundaryBlock(
        RepairPolicy.withheld(
            "No agent can free a full machine. This clears when the holders the "
            "message names finish, or a person releases one on Machine → Capacity."
        ),
        BlockKind.HARNESS,
    )
