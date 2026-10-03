"""Tables owned by initiatives: their number pool, and their schedules.

**Number pool** — one row, one counter.

Initiatives are not workspace-bound, so they cannot draw from
``workspaces.last_ticket_number``. The pool mirrors ``docker_capacity_pool``:
a singleton keyed by ``id='global'`` that ``next_initiative_number`` can
UPDATE without racing a per-workspace column.

**Schedules** — a plan per initiative, a target per milestone, the
proposals the planner agent drafts, and the planner conversation. Forecasts are not stored: they are a
function of how fast work is finishing, so a column would be stale by
definition (`services.initiative_forecast` computes them on read).
"""

from __future__ import annotations

from datetime import date, datetime
from uuid import uuid4

from loregarden.models.domain.enums import str_enum_column, utcnow
from loregarden.models.domain.plan_enums import (
    AutopilotAction,
    PlannerRole,
    PlannerTurnMode,
    PlannerTurnStatus,
    ProposalSource,
    ProposalStatus,
    ScheduleMode,
)
from sqlmodel import Field, SQLModel

#: There is one initiative counter for the whole control plane.
GLOBAL_INITIATIVE_POOL_ID = "global"


class InitiativeNumberPool(SQLModel, table=True):
    """High-water mark for initiative ``ticket_number`` values. Exactly one row."""

    __tablename__ = "initiative_number_pool"

    id: str = Field(default=GLOBAL_INITIATIVE_POOL_ID, primary_key=True)
    last_initiative_number: int = Field(default=0)


class InitiativePlan(SQLModel, table=True):
    """How one initiative's schedule behaves. At most one row per initiative;
    an initiative with no row is a FIXED plan with no notes."""

    __tablename__ = "initiative_plans"

    initiative_id: str = Field(foreign_key="tickets.id", primary_key=True)
    mode: ScheduleMode = Field(
        default=ScheduleMode.FIXED,
        sa_column=str_enum_column(ScheduleMode, ScheduleMode.FIXED),
    )
    #: The planner's reasoning for the current schedule, kept with it so the
    #: next person (or the next planner turn) knows why the dates are what they are.
    notes: str = ""
    #: Keep the plan's lanes busy: queue ready tickets as prerequisites land
    #: (`services.initiative_autopilot`). Off until someone turns it on.
    autopilot: bool = False
    #: Most tickets the autopilot keeps running or queued at once, across lanes.
    max_parallel: int = 3
    #: Why the autopilot stopped itself; blank while it has not.
    paused_reason: str = ""
    #: When autopilot was last turned on — its failure count starts here.
    autopilot_since: datetime | None = None
    updated_at: datetime = Field(default_factory=utcnow)


class ScheduleTarget(SQLModel, table=True):
    """A committed date, and an order, for one milestone (or the initiative).

    The order is the sequence the forecast assumes work proceeds in within a
    workspace. ``target_date`` null means ordered but not yet dated.
    """

    __tablename__ = "schedule_targets"

    ticket_id: str = Field(foreign_key="tickets.id", primary_key=True)
    target_date: date | None = None
    plan_order: int = 0
    updated_by: str = ""
    updated_at: datetime = Field(default_factory=utcnow)


class ScheduleProposal(SQLModel, table=True):
    """A schedule the planner drafted, waiting for a person to accept it.

    The planner never writes targets directly: it proposes, and accepting is
    the one write. ``items_json`` is a list of `ScheduleTargetInput`.
    """

    __tablename__ = "schedule_proposals"

    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True)
    initiative_id: str = Field(foreign_key="tickets.id", index=True)
    source: ProposalSource = Field(sa_column=str_enum_column(ProposalSource))
    status: ProposalStatus = Field(
        default=ProposalStatus.PENDING,
        sa_column=str_enum_column(ProposalStatus, ProposalStatus.PENDING, index=True),
    )
    #: The mode the proposal would switch the plan to; null keeps the current one.
    mode: ScheduleMode | None = Field(
        default=None, sa_column=str_enum_column(ScheduleMode, nullable=True)
    )
    rationale: str = ""
    items_json: str = "[]"
    created_at: datetime = Field(default_factory=utcnow)
    resolved_at: datetime | None = None


class InitiativePlannerMessage(SQLModel, table=True):
    """One message in an initiative's planner conversation.

    The turn's state lives on the assistant row (``status``, ``turn_mode``) so a
    restart settles it instead of leaving the composer locked.
    """

    __tablename__ = "initiative_planner_messages"

    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True)
    initiative_id: str = Field(foreign_key="tickets.id", index=True)
    role: PlannerRole = Field(sa_column=str_enum_column(PlannerRole))
    content: str = ""
    status: PlannerTurnStatus = Field(
        default=PlannerTurnStatus.COMPLETE,
        sa_column=str_enum_column(PlannerTurnStatus, PlannerTurnStatus.COMPLETE, index=True),
    )
    turn_mode: PlannerTurnMode = Field(
        default=PlannerTurnMode.CHAT,
        sa_column=str_enum_column(PlannerTurnMode, PlannerTurnMode.CHAT),
    )
    #: Ordered ChatPart JSON (see chat_primitives). Empty when the turn is plain text.
    parts_json: str = "[]"
    created_at: datetime = Field(default_factory=utcnow)


class AutopilotEvent(SQLModel, table=True):
    """One thing the autopilot did, so a person can see why work started."""

    __tablename__ = "initiative_autopilot_events"

    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True)
    initiative_id: str = Field(foreign_key="tickets.id", index=True)
    action: AutopilotAction = Field(sa_column=str_enum_column(AutopilotAction))
    ticket_id: str | None = Field(default=None, foreign_key="tickets.id")
    detail: str = ""
    created_at: datetime = Field(default_factory=utcnow, index=True)


__all__ = [
    "GLOBAL_INITIATIVE_POOL_ID",
    "InitiativeNumberPool",
    "InitiativePlan",
    "ScheduleTarget",
    "ScheduleProposal",
    "InitiativePlannerMessage",
    "AutopilotEvent",
]
