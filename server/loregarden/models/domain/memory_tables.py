"""Control-plane rows about durable memory (lg-improved-memory-178).

The learnings themselves live in the memory graph shard (662). What lives here
is what only the control plane can observe: which learnings were surfaced into
which real `agent_runs` row, and how that run ended. Keyed by the run's id and
the graph node's id — never inferred from an agent id string — so evidence
about one learning pools across every workspace it was surfaced in.
"""

from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from loregarden.models.domain.enums import str_enum_column, utcnow
from loregarden.models.domain.memory_enums import LearningOutcomeRung
from sqlalchemy import UniqueConstraint
from sqlmodel import Field, SQLModel


class LearningApplication(SQLModel, table=True):
    """One learning surfaced into one run, and — once known — that run's rung.

    Unique on (run_id, node_id): a run may assemble its briefing twice
    (supervised dispatch and `render_stage_prompt`), and counting the second
    assembly would double the evidence one run provides.

    `settled_at` NULL means the outcome is not known yet; it is not a rung. A
    settled row with `outcome` NULL is a run that concluded without any rung
    describing it (cancelled) — measured, and deliberately contributing no
    evidence either way. Neither is defaulted to a rung: an unknown outcome
    recorded as a clean pass would be a measurement nobody took.
    """

    __tablename__ = "learning_applications"
    __table_args__ = (UniqueConstraint("run_id", "node_id", name="uq_learning_application"),)

    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True)
    run_id: str = Field(foreign_key="agent_runs.id", index=True)
    briefing_id: str | None = Field(default=None, foreign_key="memory_briefings.id")
    node_id: str = Field(index=True)
    workspace_id: str = Field(foreign_key="workspaces.id")
    ticket_id: str | None = Field(default=None, foreign_key="tickets.id", index=True)
    stage_key: str = ""
    #: Position in the briefing's learnings list, 0-based.
    position: int = 0
    outcome: LearningOutcomeRung | None = Field(
        default=None, sa_column=str_enum_column(LearningOutcomeRung, nullable=True)
    )
    settled_at: datetime | None = Field(default=None, index=True)
    created_at: datetime = Field(default_factory=utcnow)


class MemoryHealthSnapshot(SQLModel, table=True):
    """One recorded reading of a workspace memory graph's shape. Append-only.

    Counts, not shares: the share is derived against `learnings` when read, so
    a later change to how shares are rounded never rewrites history.
    """

    __tablename__ = "memory_health_snapshots"

    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True)
    workspace_slug: str = Field(index=True)
    taken_at: datetime = Field(default_factory=utcnow, index=True)
    learnings: int
    unlinked: int
    never_surfaced: int
    surfaced_unscored: int
    stale: int
    contested: int
    superseded: int
    discredited: int
