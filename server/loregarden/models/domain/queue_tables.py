"""The queue's own tables: lane slots, parked entries, and saved snapshots.

Split out of ``tables`` when a typed `entry_kind` pushed that module past its
1500-line cap. This is the group that came out because it is the one being
edited, and it is cohesive: `queued_runs` holds two regimes at once (a lane entry
and a shared-queue entry), `agent_slots` records which of them a lane is running,
and `queue_snapshots` stores a whole queue's state. They are read and written
together and by nothing else.

Re-exported from ``models.domain``, so every existing import site is unchanged.

ONE CONSEQUENCE WORTH KNOWING. Moving these classes changes mapper configuration
order, and these models are joined to `agent_runs` and `orchestration_runs` by
bare foreign key columns with no `Relationship` between them — so SQLAlchemy can
emit a child INSERT before its parent in the same flush. Two queue tests were
adding both at once and relying on the old order; they now commit the parent
first, which is what ``tests/factories.py`` has always said to do.
"""

from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from loregarden.models.domain.enums import (
    QueueEntryKind,
    QueuePosition,
    RepairRoute,
    str_enum_column,
    utcnow,
)
from sqlmodel import Field, SQLModel


class AgentSlot(SQLModel, table=True):
    __tablename__ = "agent_slots"

    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True)
    #: Null on every slot in the shared pool. Capacity belongs to the machine,
    #: not to a workspace — the column survives only for rows written before
    #: migration 0058 collapsed the per-workspace pools.
    workspace_id: str | None = Field(default=None, foreign_key="workspaces.id", index=True)
    #: Unique, because the claim keys on it conceptually and the pool's size is
    #: the machine's concurrency limit. Two threads initialising an empty pool
    #: both inserted a full set, giving six slots for a limit of three — the
    #: admission gate's whole purpose, doubled silently.
    slot_number: int = Field(default=1, unique=True)
    is_available: bool = True
    current_run_id: str | None = Field(default=None, foreign_key="agent_runs.id")
    #: The orchestration occupying this lane. A lane runs a whole ticket, which
    #: spans many agent runs, so this — not `current_run_id` — is what holds the
    #: lane for the duration.
    current_orchestration_run_id: str | None = Field(
        default=None, foreign_key="orchestration_runs.id"
    )
    assigned_at: datetime | None = None
    released_at: datetime | None = None


class QueuedRun(SQLModel, table=True):
    __tablename__ = "queued_runs"

    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True)
    workspace_id: str = Field(foreign_key="workspaces.id", index=True)
    ticket_id: str = Field(foreign_key="tickets.id", index=True)
    #: Null until this entry starts — a lane entry is a ticket waiting its turn,
    #: and nothing runs on its behalf before then.
    run_id: str | None = Field(default=None, foreign_key="agent_runs.id", index=True)
    orchestration_run_id: str | None = Field(
        default=None, foreign_key="orchestration_runs.id", index=True
    )
    #: Which lane this entry waits in. Each slot is its own serial pipeline.
    slot_number: int = Field(default=1, index=True)
    #: Order *within the lane*, not across the board.
    position: int = 0
    #: Answers from the dialog that queued this, honoured whenever the lane
    #: reaches it — which may be long after that dialog closed.
    auto_approve: bool = False
    stop_at_stage_key: str = ""
    #: Whether this entry runs the whole ticket or one stage of it. Typed, not a
    #: bare string: this is the discriminator between the two regimes sharing
    #: `queued_runs`, so a typo in a comparison against it reads as "the other
    #: kind" rather than as an error (lg-workflow-integrity-667). Values are
    #: unchanged, so no migration.
    entry_kind: QueueEntryKind = Field(
        default=QueueEntryKind.ORCHESTRATION,
        sa_column=str_enum_column(QueueEntryKind, QueueEntryKind.ORCHESTRATION),
    )
    #: The stage to run, for a "stage" entry.
    stage_key: str = ""
    #: Overrides the caller asked for, held because the entry is the only record
    #: of the ask by the time a lane reaches it. Empty/None means the workspace's
    #: orchestration profile decides.
    driver: str = ""
    max_stages: int | None = None
    #: Max seconds each agent run in this orchestration may take. Null = agent default.
    timeout_seconds: int | None = None
    #: Spend one dispatch past an exhausted stage retry budget when this entry
    #: starts. Carried for the same reason the overrides above are: the decision
    #: was made when the request was filed, and a lane reaching the entry hours
    #: later has no other record of it. Without this the refusal simply fired
    #: again at promotion, into `dispatch_stage`'s warning log.
    force: bool = False
    status: QueuePosition = Field(
        default=QueuePosition.QUEUED,
        sa_column=str_enum_column(QueuePosition, QueuePosition.QUEUED, index=True),
    )
    retry_count: int = 0
    max_retries: int = 3
    #: Why this entry's block was judged provisional the last time it blocked,
    #: or null because it never has. Null and "no route" are the same fact here
    #: — an entry that has never repaired has nothing to say about how.
    repair_route: RepairRoute | None = Field(
        default=None,
        sa_column=str_enum_column(RepairRoute, None, nullable=True),
    )
    #: How many times this entry has been re-dispatched out of a repair hold.
    #: Counted per entry and never reset: it bounds how long one lane occupancy
    #: may keep repairing itself, which a per-stage counter cannot.
    repair_attempts: int = 0
    #: When the current hold began; null whenever the entry is not holding. The
    #: wall-clock cap is measured from here, so it starts fresh for each hold
    #: rather than aging across the run that the last repair bought.
    repairing_since: datetime | None = None
    estimated_start_at: datetime | None = None
    promoted_at: datetime | None = None
    started_at: datetime | None = None
    failure_reason: str = ""
    last_failed_at: datetime | None = None
    #: When someone acknowledged this entry's blocked/failed outcome on the lane
    #: card. Null while it still needs attention — the lane keeps showing it.
    dismissed_at: datetime | None = None
    created_at: datetime = Field(default_factory=utcnow)


class QueueSnapshot(SQLModel, table=True):
    __tablename__ = "queue_snapshots"

    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True)
    workspace_id: str = Field(foreign_key="workspaces.id", index=True)
    name: str = ""
    description: str = ""
    queue_state_json: str = "[]"
    stats_json: str = "{}"
    tags: str = ""
    created_by: str = ""
    created_at: datetime = Field(default_factory=utcnow)
