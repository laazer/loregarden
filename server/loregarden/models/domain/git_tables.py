"""The tables the git automation pipeline writes: CI, autofix, worktrees.

Split out of ``tables`` when the stage-park waiver columns pushed that module
past its 1500-line cap — the same reason and the same shape as
``queue_tables`` and ``docker_tables``.

This is the group that came out because it is cohesive rather than merely
adjacent: a ticket's branch is built in a `Worktree`, its push is judged by a
`CIRunResult`, and a red result is retried through an `AutoFixAttempt`. They are one pipeline's
bookkeeping, written by `services/git_*` and read by nothing else in the model
layer.

Re-exported from ``models.domain``, so every existing import site is unchanged.
Note the ordering caveat ``queue_tables`` documents: moving classes changes
mapper configuration order, and these are joined to `tickets` by bare foreign
key columns with no `Relationship`, so a test adding a child and its parent in
one flush must commit the parent first.
"""

from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from loregarden.models.domain.enums import (
    AutoFixStatus,
    CIStatus,
    WorktreeState,
    str_enum_column,
    utcnow,
)
from sqlmodel import Field, SQLModel


class CIRunResult(SQLModel, table=True):
    __tablename__ = "ci_run_results"

    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True)
    workspace_id: str = Field(foreign_key="workspaces.id", index=True)
    ticket_id: str = Field(foreign_key="tickets.id", index=True)
    status: CIStatus = Field(
        default=CIStatus.PENDING,
        sa_column=str_enum_column(CIStatus, CIStatus.PENDING, index=True),
    )
    provider: str = ""
    #: The commit these checks ran on. One commit is one result: the poller
    #: updates this row as checks settle and arms an auto-fix once per commit.
    head_sha: str = ""
    pr_number: int | None = None
    external_run_id: str | None = None
    logs_url: str | None = None
    failure_summary: str | None = None
    full_logs: str | None = None
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class AutoFixAttempt(SQLModel, table=True):
    __tablename__ = "auto_fix_attempts"

    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True)
    ci_run_result_id: str = Field(foreign_key="ci_run_results.id", index=True)
    attempt_number: int = 1
    run_id: str | None = Field(default=None, foreign_key="agent_runs.id")
    status: AutoFixStatus = Field(
        default=AutoFixStatus.PENDING,
        sa_column=str_enum_column(AutoFixStatus, AutoFixStatus.PENDING, index=True),
    )
    result_summary: str | None = None
    created_at: datetime = Field(default_factory=utcnow)
    completed_at: datetime | None = None


class Worktree(SQLModel, table=True):
    __tablename__ = "worktrees"

    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True)
    workspace_id: str = Field(foreign_key="workspaces.id", index=True)
    agent_run_id: str = Field(foreign_key="agent_runs.id", index=True)
    #: Set when the worktree belongs to a ticket rather than to one run. A
    #: ticket's stages share one tree, so reuse is a lookup on this column;
    #: `agent_run_id` stays as provenance for whichever run cut it. Null for
    #: the fan-out and parallel-queue paths, where a run really does want its
    #: own tree.
    ticket_id: str | None = Field(default=None, foreign_key="tickets.id", index=True)
    #: Set when the worktree belongs to a Home chat thread. Same role as
    #: `ticket_id` one field up — the reuse key for a conversation whose acting
    #: turns must see each other's work — for the surface that has no ticket to
    #: hang one off. Exactly one of the two is set, or neither for a run that
    #: owns its tree outright.
    #: `SET NULL` on delete, unlike `ticket_id` above: a deleted thread's tree is
    #: released first (`worktree_lifecycle.release_chat_worktree`), and what is
    #: left is a retired row kept for provenance. Without this the foreign key
    #: refuses the delete over a row nothing is using any more.
    chat_session_id: str | None = Field(
        default=None,
        foreign_key="baxter_chat_sessions.id",
        ondelete="SET NULL",
        index=True,
    )
    parent_branch: str = "main"
    worktree_path: str = ""
    state: WorktreeState = Field(
        default=WorktreeState.ACTIVE,
        sa_column=str_enum_column(WorktreeState, WorktreeState.ACTIVE, index=True),
    )
    #: The branch checked out in this worktree, as opposed to `parent_branch`
    #: (what it was cut from) or the directory name, which is named
    #: after the run and is not a ref.
    branch: str = ""
    merge_base: str | None = None
    #: The durable claim on this checkout: the pid that cut or reused it, the
    #: process start time that tells a reused pid from the original, and the
    #: boot both were read under. Three columns rather than one blob so a
    #: half-written claim is visible as one (`process_identity.parse_claim`),
    #: and all three default to `''` because every row written before
    #: lg-external-harness-1026 has no claim — read as "nobody claims this",
    #: which is the single deliberate fail-open in that change and is bounded
    #: by the receipt premise beside it.
    claim_pid: str = ""
    claim_birth: str = ""
    claim_boot_id: str = ""
    #: Why the last sweep did not remove this checkout, in the words it
    #: reported. Evidence records uncertainty rather than releasing ownership,
    #: and the row is the only place that answer survives the sweep that
    #: produced it — so it is written on every refusal and cleared the moment
    #: the tree is settled, because a reason describing a condition that has
    #: since resolved sends an operator after the wrong thing.
    unresolved_reason: str = ""
    #: Where a failed removal left the checkout: `release_directory` renames
    #: before it deletes, so a delete that failed and could not be undone has
    #: the bytes under an unguessable name and this is the only record of it.
    parked_at: str = ""
    created_at: datetime = Field(default_factory=utcnow)
    cleaned_at: datetime | None = None


class IntegrationBranchMove(SQLModel, table=True):
    """One move of a branch, written by `integration_branch_moves` as it moves it.

    Append-only. The reflog says a ref moved; this says which writer moved it,
    why, and under which run, and it outlives the branch: deleting and
    recreating `integration/lg-milestone-that-491` took its reflog with it, and
    with that the only record of who dropped lg-milestone-that-1316's landing.
    ``new_tip`` empty is a deletion; ``old_tip`` empty is a creation.
    """

    __tablename__ = "integration_branch_moves"

    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True)
    workspace_id: str = Field(foreign_key="workspaces.id", index=True)
    branch: str = Field(index=True)
    old_tip: str = ""
    new_tip: str = ""
    #: The writer that moved it, as ``module.function``.
    writer: str
    why: str
    run_id: str | None = None
    moved_at: datetime = Field(default_factory=utcnow, index=True)
