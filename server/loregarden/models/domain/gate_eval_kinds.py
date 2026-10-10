"""Whose fault a failed transition gate was, for scoring the agent that faced it.

A gate failure is evidence about an agent only when the agent could have
prevented it. A uv warning, a 300s timeout or a broken worktree fails the same
gate a real organization violation does, and counting them alike makes a model
look worse for the machine it ran on. These kinds separate the two so an eval
built on `GateEvaluated` events scores the work, not the harness.

Deliberately the same words as `BlockKind` where the meaning is the same:
`harness` and `work` here mean what they mean for a block, so a report that
joins the two does not need a translation table. `inherited` has no block
analogue — it is the gate-level case of `GateFaultAttribution.FOREIGN`, decided
by stage rather than by path, because historical events carry no path sets.
"""

from __future__ import annotations

from enum import StrEnum

from loregarden.models.domain.block_kinds import BlockKind


class GateFailureKind(StrEnum):
    """Who a failed gate evaluation is evidence against."""

    #: The machine, not the agent: timeouts, broken worktrees, tool noise.
    HARNESS = BlockKind.HARNESS.value
    #: The agent's own output failed a check it could have passed.
    WORK = BlockKind.WORK.value
    #: A work-shaped failure at a stage that writes no code — the files it
    #: names were somebody else's.
    INHERITED = "inherited"
    #: No rule matched. Reported so the rules can grow, never scored as work.
    UNKNOWN = "unknown"


class GateFailureCategory(StrEnum):
    """Which rule classified a failure — the finer grain behind the kind."""

    TIMEOUT = "timeout"
    BROKEN_WORKTREE = "broken_worktree"
    VIRTUAL_ENV_MISMATCH = "virtual_env_mismatch"
    TASK_HELP_LISTING = "task_help_listing"
    #: `GateOutcome.UNAVAILABLE` with no more specific rule: the gate could not run.
    UNAVAILABLE = "unavailable"
    FORMAT = "format"
    PY_ORGANIZATION = "py_organization"
    TS_ORGANIZATION = "ts_organization"
    TODO_VALIDATION = "todo_validation"
    LINT_DIFF = "lint_diff"
    UNCLASSIFIED = "unclassified"
