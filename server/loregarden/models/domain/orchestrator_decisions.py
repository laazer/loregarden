"""What the orchestrator decided, when it decided something on its own.

Its own module rather than a block in `enums.py`, which sits at the module
size cap: a closed vocabulary with one owner and four members is a cohesive
thing to hold apart, and `models.domain.__init__` star-exports it the same
way it exports every sibling.
"""

from __future__ import annotations

from enum import StrEnum


class OrchestratorDecision(StrEnum):
    """What the orchestrator decided, when it decided something on its own.

    Deliberately closed. Each is a shape that has actually cost a run here and
    was, until lg-workflow-integrity-734, visible only as a server log line the
    UI never read. A kind added here is a kind the readers must learn to render.
    """

    #: A stage was not dispatched because its parent orchestration was already
    #: terminal — the run 688 stopped happening 25 seconds after a reap.
    REFUSED_DISPATCH_TERMINAL_PARENT = "refused_dispatch_terminal_parent"
    #: A run left in flight under a terminal parent was settled, and the retry
    #: attempt it never spent was handed back (697).
    SETTLED_ORPHANED_RUN = "settled_orphaned_run"
    #: A stage stuck RUNNING with no live run behind it was settled to blocked.
    SETTLED_STRANDED_STAGE = "settled_stranded_stage"
    #: A handoff was stored despite the workspace gate not knowing the pair,
    #: because the ticket's own template confirmed it (730).
    OVERRULED_STALE_GATE = "overruled_stale_gate"
    #: The sign-off gate on a design/plan stage was approved by the run itself,
    #: as the run modal allowed (746). A person can still see it in the history.
    APPROVED_DESIGN_PLAN = "approved_design_plan"
    #: A block was given a kind — who can unblock it (749). The history line
    #: says which, and whether the agent said so or the message decided.
    CLASSIFIED_BLOCK = "classified_block"
    #: A person answered a decision block's question; the choice is on the
    #: ticket and the stage was requeued without anyone asking (749).
    REQUEUED_AFTER_DECISION = "requeued_after_decision"
    #: A harness/work block got its one repair turn: the stage was re-armed
    #: under the repair agent instead of waiting for a person (750).
    DISPATCHED_REPAIR = "dispatched_repair"
    #: The repair turn passed; the block is gone and the workflow carried on.
    REPAIRED = "repaired"
    #: No repair, or no second one: the block goes to a person with its kind.
    REPAIR_ESCALATED = "repair_escalated"
    #: A lane read busy with nothing in it — `is_available=0`, both occupant
    #: ids null, past the reservation grace — and was given back to the pool
    #: (775). Slot 1 sat that way for hours while the reclaim was a log line.
    RECLAIMED_GHOST_LANE = "reclaimed_ghost_lane"
