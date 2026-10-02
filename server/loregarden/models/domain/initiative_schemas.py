"""Read models for initiatives — the cross-workspace parent above milestones.

An initiative's children live in different workspaces, so no workspace-scoped
ticket list or tree can show one whole. These views are assembled without a
workspace filter and name each milestone's workspace instead.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from loregarden.models.domain.enums import TicketState
from loregarden.models.domain.plan_enums import (
    AutopilotAction,
    ForecastBasis,
    NodeStatus,
    ProposalSource,
    ScheduleMode,
    ScheduleStatus,
)
from pydantic import BaseModel, Field


class InitiativeMilestoneView(BaseModel):
    id: str
    external_id: str
    title: str
    state: TicketState
    workspace_slug: str


class InitiativeProgress(BaseModel):
    #: Milestones in `done` or `wont_do` — the same resolution the rollup uses.
    resolved: int
    total: int


class InitiativeView(BaseModel):
    id: str
    external_id: str
    title: str
    description: str
    state: TicketState
    priority: int
    milestones: list[InitiativeMilestoneView]
    progress: InitiativeProgress
    #: Distinct workspaces the milestones live in, sorted.
    workspaces: list[str]


class WorkspacePace(BaseModel):
    """How fast one workspace's share of an initiative is finishing."""

    workspace_slug: str
    #: Resolved work items per day over the window; null when none resolved.
    per_day: float | None
    #: Work items resolved inside the window — the sample `per_day` stands on.
    completed: int
    basis: ForecastBasis


class MilestoneSchedule(BaseModel):
    id: str
    external_id: str
    title: str
    state: TicketState
    workspace_slug: str
    plan_order: int
    target_date: date | None
    #: When its last open item lands, scheduled through the dependency graph
    #: (`services.initiative_graph`). Null when any item cannot be priced.
    forecast_date: date | None
    #: The date the plan shows: the target in a fixed plan, the forecast in a
    #: rolling one.
    planned_date: date | None
    #: Forecast minus target, in days. Positive is late.
    drift_days: int | None
    status: ScheduleStatus
    basis: ForecastBasis
    #: Open work items under the milestone, and all of them.
    remaining: int
    total: int
    #: Open items by graph status: ready, running, waiting, needs_person, blocked.
    counts: dict[NodeStatus, int]
    #: Items whose duration was the plan's median, not a measurement.
    assumed: int


class ScheduleTargetInput(BaseModel):
    """One row of a schedule edit or proposal.

    Omitting ``target_date`` leaves the date as it is; sending null clears it.
    """

    ticket_id: str
    target_date: date | None = None
    #: Null or omitted keeps the current order.
    plan_order: int | None = None


class ScheduleProposalView(BaseModel):
    id: str
    source: ProposalSource
    mode: ScheduleMode | None
    rationale: str
    #: `ScheduleTargetInput` rows as sent: a key that is absent was not proposed.
    items: list[dict[str, Any]]
    created_at: datetime


class InitiativePlanView(BaseModel):
    id: str
    external_id: str
    title: str
    description: str
    state: TicketState
    mode: ScheduleMode
    notes: str
    target_date: date | None
    forecast_date: date | None
    planned_date: date | None
    drift_days: int | None
    status: ScheduleStatus
    #: Open milestones with no forecast; when non-zero the initiative's
    #: forecast is null rather than the latest of the ones that could be priced.
    unforecast_milestones: int
    milestones: list[MilestoneSchedule]
    paces: list[WorkspacePace]
    #: The trailing window, in days, every pace was measured over.
    window_days: int
    pending_proposal: ScheduleProposalView | None
    #: Every ticket in the graph — the initiative's work and outside prerequisites.
    nodes: list[PlanNodeView]
    #: Ticket ids on the critical path, first to last.
    critical_path: list[str]
    #: Ticket ids caught in a dependency cycle (cannot be scheduled).
    cyclic: list[str]
    lanes: list[str]
    autopilot: AutopilotView
    generated_at: datetime


class InitiativePlanUpdate(BaseModel):
    """An operator's direct edit. Omitted fields are left as they are."""

    mode: ScheduleMode | None = None
    notes: str | None = None
    targets: list[ScheduleTargetInput] = []


class ScheduleProposalCreate(BaseModel):
    """What the planner submits. Items may name the initiative or its milestones."""

    rationale: str
    items: list[ScheduleTargetInput]
    mode: ScheduleMode | None = None


class PlanNodeView(BaseModel):
    """One ticket in the plan's dependency graph."""

    id: str
    external_id: str
    title: str
    workspace_slug: str
    state: TicketState
    status: NodeStatus
    lane: str
    #: Its milestone; null for a prerequisite outside the initiative.
    milestone_id: str | None
    #: Dependency depth among open work: 1 means nothing open before it.
    step: int
    deps: list[str]
    #: Prerequisites not yet done.
    waiting_on: list[str]
    start: datetime | None
    finish: datetime | None
    duration_days: float | None
    basis: ForecastBasis
    assumed: bool
    critical: bool
    external: bool


class AutopilotEventView(BaseModel):
    action: AutopilotAction
    ticket_id: str | None
    ticket_external_id: str | None
    detail: str
    created_at: datetime


class AutopilotView(BaseModel):
    enabled: bool
    max_parallel: int
    #: Why it stopped itself; blank when it has not.
    paused_reason: str
    #: Plan tickets holding a lane right now (running, queued, or awaiting approval).
    in_flight: int
    #: Ready tickets it would start next, in the order it would start them.
    next_up: list[str]
    #: False on a sandbox server, where the loop never runs.
    available: bool
    recent: list[AutopilotEventView]


class AutopilotUpdate(BaseModel):
    enabled: bool | None = None
    max_parallel: int | None = Field(default=None, ge=1, le=12)
