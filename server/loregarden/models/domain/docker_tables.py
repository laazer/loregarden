"""The docker capacity ledger: one claim per row, plus the pool it draws from.

**Two tables, and the split is not the one `queued_runs` made.** `docker_leases`
holds waiters and holders together, because a waiter *becomes* a holder in
place: same owner, same weights, same TTL, same docker identity. `queued_runs`
needed `entry_kind` to tell two genuinely different regimes apart; this is one
lifecycle, and splitting it would put a delete and an insert either side of every
grant — precisely where a double-booking would hide. Ended rows stay as the audit
trail the reaper's warnings point at.

`docker_capacity_pool` is a single row (`id = 'global'`) and exists for one
reason: something a conditional UPDATE can key on. Admission has to decide
"does this claim fit" and "grant it" in one statement, and there is no SQLite
statement that both sums a ledger and inserts conditionally on that sum —
`SELECT SUM(...)` then `INSERT` is the select-then-mutate defect `claim_free_slot`
was written to remove, one aggregate wider. So the pool carries running totals
and a `revision` counter, and the claim is a single UPDATE whose WHERE clause
holds every dimension at once.

**The ledger stays authoritative; the pool is a derived index over it.** Every
reap pass recomputes the totals from the rows in one statement, so a process
killed between the pool update and the row commit leaks capacity for at most one
sweep rather than forever.

The ceiling lives on the pool row too, rather than being passed in per claim: if
each racer supplied its own locally cached number, two processes with different
cache ages would enforce different limits. Comparing against a column means
every racer is judged by one number.

SAME MAPPER-ORDER CAVEAT AS `queue_tables`: these carry bare foreign key columns
with no `Relationship`, so a flush can emit a lease INSERT before the run it
names. Tests must commit parent runs and tickets first — `tests/factories.py`
has always said so.
"""

from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from loregarden.models.domain.enums import (
    DockerCeilingSource,
    DockerFootprint,
    DockerHolderKind,
    DockerLeaseEndReason,
    DockerLeaseStatus,
    str_enum_column,
    utcnow,
)
from sqlmodel import Field, SQLModel

#: There is one machine, so there is one pool. Named rather than implied, so a
#: query reading the wrong row is a typo rather than a silent empty result.
GLOBAL_POOL_ID = "global"


class DockerCapacityPool(SQLModel, table=True):
    """Running totals and the ceiling in force. Exactly one row.

    `revision` is what tells "lost a race" apart from "genuinely full": a
    conditional UPDATE that matched no row could mean either, and retrying is
    right for the first and wrong for the second.
    """

    __tablename__ = "docker_capacity_pool"

    id: str = Field(default=GLOBAL_POOL_ID, primary_key=True)

    #: Derived from the ledger. Repaired from it at the end of every reap pass.
    held_cpus: float = 0.0
    held_memory_mb: int = 0
    held_count: int = 0

    ceiling_cpus: float = 0.0
    ceiling_memory_mb: int = 0
    ceiling_leases: int = 0
    ceiling_source: DockerCeilingSource = Field(
        default=DockerCeilingSource.UNKNOWN,
        sa_column=str_enum_column(DockerCeilingSource, DockerCeilingSource.UNKNOWN),
    )
    #: When the measurement behind the ceiling was taken. Null while it has never
    #: been measured, which is not the same as measured-as-zero.
    probed_at: datetime | None = None
    #: Why the last probe failed, carried so every payload quoting a stale
    #: ceiling can say what went wrong rather than just that it is old.
    probe_error: str = ""

    #: Bumped by every successful claim and release, so a racer can tell whether
    #: the pool moved under its read.
    revision: int = 0
    #: Handed out to waiters. Monotonic and global, so two concurrent waiters
    #: cannot both take position 7.
    next_position: int = 1


class DockerLease(SQLModel, table=True):
    """One claim on docker capacity, from request through to release."""

    __tablename__ = "docker_leases"

    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True)
    status: DockerLeaseStatus = Field(
        default=DockerLeaseStatus.WAITING,
        sa_column=str_enum_column(DockerLeaseStatus, DockerLeaseStatus.WAITING, index=True),
    )

    holder_kind: DockerHolderKind = Field(
        default=DockerHolderKind.AD_HOC,
        sa_column=str_enum_column(DockerHolderKind, DockerHolderKind.AD_HOC),
    )
    #: Free text naming who asked. Required in practice: an unlabelled lease on
    #: a full board tells an operator nothing about what to go and stop.
    holder_label: str = ""
    agent_run_id: str | None = Field(default=None, foreign_key="agent_runs.id", index=True)
    orchestration_run_id: str | None = Field(default=None, foreign_key="orchestration_runs.id")
    ticket_id: str | None = Field(default=None, foreign_key="tickets.id")
    workspace_id: str | None = Field(default=None, foreign_key="workspaces.id")
    #: A shell the lease was taken from, when the caller supplied one. A pid that
    #: no longer exists settles liveness outright, with no docker call at all.
    holder_pid: int | None = None

    footprint: DockerFootprint = Field(
        default=DockerFootprint.CUSTOM,
        sa_column=str_enum_column(DockerFootprint, DockerFootprint.CUSTOM),
    )
    #: The RESOLVED price, not re-derived from `footprint` later. Retuning the
    #: weights table must not retroactively change what a live lease is
    #: accounted at.
    cpus: float = 0.0
    memory_mb: int = 0

    #: What the holder actually started, stamped after the fact. This is what
    #: turns TTL-only reaping into probe-gated reaping: a lease naming nothing
    #: has only its clock to be judged by.
    compose_project: str = ""
    container_names_json: str = "[]"

    #: FIFO order among waiters. Strictly head-of-line: a large claim at the head
    #: is not skipped by a small one behind it.
    position: int = 0
    ttl_seconds: int = 900

    requested_at: datetime = Field(default_factory=utcnow)
    granted_at: datetime | None = None
    expires_at: datetime | None = Field(default=None, index=True)
    last_renewed_at: datetime | None = None
    released_at: datetime | None = None
    end_reason: DockerLeaseEndReason | None = Field(
        default=None,
        sa_column=str_enum_column(DockerLeaseEndReason, nullable=True),
    )

    #: What the last liveness probe found, and why it found nothing when it
    #: failed. Recorded rather than discarded because an expired lease held open
    #: by an unreachable daemon and one held open by a running stack look
    #: identical on the board otherwise.
    last_probe_at: datetime | None = None
    last_probe_outcome: str = ""
    last_probe_error: str = ""
    running_container_count: int | None = None

    note: str = ""
    created_at: datetime = Field(default_factory=utcnow)
