"""What a review, verify or gate stage must be given before its agent is spawned.

Its own module rather than a block in `enums.py`, which sits at the module size
cap; `models.domain.__init__` star-exports it like every sibling.
"""

from __future__ import annotations

from enum import StrEnum


class StageInput(StrEnum):
    """An input a stage declares in `WorkflowStageDef.required_inputs`.

    Checked by `services.stage_readiness` before the agent is spawned, through
    the same source the agent reads, so a missing one blocks the stage at zero
    tokens instead of costing a turn to discover.
    """

    #: The ticket's branch differs from the commit it was cut from: there is work
    #: to review. Read with git in the ticket's worktree, as a reviewer reads it.
    DIFF = "diff"
    #: The ticket states at least one acceptance criterion to verify against.
    ACCEPTANCE_CRITERIA = "acceptance_criteria"
