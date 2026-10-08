"""Vocabulary for initiative schedules: how a plan moves, and how it is doing.

A plan holds a *target* date per milestone — what someone committed to — and
the control plane recomputes a *forecast* from how fast the work is actually
finishing. These enums name the two choices that separate them.
"""

from __future__ import annotations

from enum import StrEnum


class ScheduleMode(StrEnum):
    """Which date the plan shows as *the* date."""

    #: Targets stay put until someone re-baselines; the forecast is drawn
    #: beside them as ahead or behind.
    FIXED = "fixed"
    #: The planned date follows the forecast, so the schedule slides as work
    #: speeds up or slows down. Targets are kept as the baseline it drifts from.
    ROLLING = "rolling"
    #: No official dates: the plan is where the work will likely land at the
    #: measured pace. Targets are kept but not in force, so switching back
    #: restores them.
    PACE = "pace"


class ScheduleStatus(StrEnum):
    """One milestone's standing against its target."""

    DONE = "done"
    #: Forecast lands on or before the target.
    ON_TRACK = "on_track"
    #: Forecast lands after the target, which has not passed yet.
    BEHIND = "behind"
    #: The target has passed and the work is still open.
    LATE = "late"
    #: Open, but nobody has set a target.
    UNSCHEDULED = "unscheduled"
    #: A target exists but nothing measured can price the remaining work —
    #: or, in pace mode, there is no measured pace to project from.
    NO_FORECAST = "no_forecast"
    #: Pace mode: open, projected from the measured pace, with no target to
    #: be ahead of or behind.
    PACED = "paced"


class ForecastBasis(StrEnum):
    """What a forecast date was computed from — said, so it can be weighed."""

    #: Resolved work items per day in this initiative's own subtree.
    INITIATIVE_THROUGHPUT = "initiative_throughput"
    #: Too little history in the initiative; the whole workspace's pace instead.
    WORKSPACE_THROUGHPUT = "workspace_throughput"
    #: Agent run-time for the remaining stages was longer than the throughput
    #: forecast, so it set the date — work cannot finish faster than it runs.
    AGENT_TIME = "agent_time"
    #: Nothing to measure: no completions in the window and no run history.
    NONE = "none"


class ProposalStatus(StrEnum):
    PENDING = "pending"
    ACCEPTED = "accepted"
    DISCARDED = "discarded"
    #: A newer proposal for the same initiative replaced it before anyone chose.
    SUPERSEDED = "superseded"


class ProposalSource(StrEnum):
    """Who drafted a proposal."""

    #: The one-shot "Draft schedule" run.
    DRAFT = "draft"
    #: The planner chat, mid-conversation.
    CHAT = "chat"


class PlannerRole(StrEnum):
    USER = "user"
    ASSISTANT = "assistant"


class PlannerTurnStatus(StrEnum):
    """An assistant row's lifecycle; user rows are written COMPLETE."""

    PENDING = "pending"
    COMPLETE = "complete"
    FAILED = "failed"


class PlannerTurnMode(StrEnum):
    """Which prompt a planner turn runs with."""

    CHAT = "chat"
    #: The "Draft schedule" button: plan the whole initiative and propose it.
    DRAFT = "draft"


class NodeStatus(StrEnum):
    """Where one work item stands in the plan's dependency graph."""

    DONE = "done"
    #: An agent run is executing or queued for it.
    RUNNING = "running"
    #: Every prerequisite is done; it can start now.
    READY = "ready"
    #: Waiting for prerequisites to finish.
    WAITING = "waiting"
    #: A person has to act: tagged `needs-person`, parked, or blocked on a
    #: decision or a human action. Autopilot never dispatches these.
    NEEDS_PERSON = "needs_person"
    #: Stuck for a reason an agent or the harness may clear (block_kind work/harness).
    BLOCKED = "blocked"


class AutopilotAction(StrEnum):
    """What the autopilot did, for its log."""

    ENABLED = "enabled"
    DISABLED = "disabled"
    #: Queued a ready ticket into a free lane.
    DISPATCHED = "dispatched"
    #: The queue refused a ticket it tried to dispatch.
    REFUSED = "refused"
    #: Stopped itself: too many of the tickets it started ended blocked.
    PAUSED = "paused"


class SuggestionKind(StrEnum):
    """What a suggested initiative groups."""

    #: Milestones that share a goal.
    THEME = "theme"
    #: A time-box: open features and bugs sized to the measured pace.
    SPRINT = "sprint"


class SuggestionSource(StrEnum):
    """Who drew up a set of initiative suggestions."""

    #: Instant keyword grouping, computed on read.
    HEURISTIC = "heuristic"
    #: One agent turn over the same candidates.
    AGENT = "agent"
