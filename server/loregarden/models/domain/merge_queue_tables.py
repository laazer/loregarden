"""The merge queue's own state: one row per candidate for a workspace's base branch.

lg-milestone-that-968. The loop (`services.merge_queue_loop`) reads these rows
and is the only writer past admission; admission and withdrawal are
`services.merge_queue`. Nothing here talks to git or GitHub.

A candidate is a pull request: for a tree, the root's
``publish/integration/<root>`` PR that `publish_tree` opens; otherwise any open
PR on the base, admitted by number with no ticket (an interactive session's
branch), whose head the queue pushes its staged commit onto. ``head_sha`` is
the work being merged (the integration branch's tip at admission), not the PR's
head commit: the loop re-cuts the PR's head onto the base whenever the base
moves, so the PR head changes under one candidate while ``head_sha`` does not.

Two indexes carry the admission rules the service also checks, so a race
between two admissions is refused by the database rather than won by both:

- ``(workspace_id, position)`` is unique: two concurrent admissions cannot
  claim one position.
- ``(workspace_id, head_sha)`` is unique among live entries: the same work
  cannot be queued twice.
"""

from __future__ import annotations

import json
from datetime import datetime
from uuid import uuid4

from loregarden.models.domain.enums import str_enum_column, utcnow
from loregarden.models.domain.merge_queue_enums import MergeQueueState, MergeQueueWait
from sqlalchemy import Index, UniqueConstraint, text
from sqlmodel import Field, SQLModel


class MergeQueueEntry(SQLModel, table=True):
    __tablename__ = "merge_queue_entries"
    __table_args__ = (
        UniqueConstraint("workspace_id", "position", name="uq_merge_queue_position"),
        Index(
            "uq_merge_queue_live_head",
            "workspace_id",
            "head_sha",
            unique=True,
            sqlite_where=text("state NOT IN ('merged', 'failed', 'timed_out', 'withdrawn')"),
        ),
    )

    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True)
    workspace_id: str = Field(foreign_key="workspaces.id", index=True)
    #: The tree's root ticket; None for a pull request admitted by number.
    ticket_id: str | None = Field(default=None, foreign_key="tickets.id", index=True)
    pr_number: int
    pr_url: str = ""
    #: The PR's head branch on the remote (``publish/<branch>``): the staging ref.
    pr_head_branch: str = ""
    #: The local branch whose work this carries (the integration branch).
    source_branch: str = ""
    #: The work being merged, fixed for the life of the entry.
    head_sha: str
    base_branch: str = "main"
    #: FIFO order within the workspace. Unique; never reused.
    position: int
    state: MergeQueueState = Field(
        default=MergeQueueState.QUEUED,
        sa_column=str_enum_column(MergeQueueState, MergeQueueState.QUEUED, index=True),
    )
    wait: MergeQueueWait = Field(
        default=MergeQueueWait.NONE,
        sa_column=str_enum_column(MergeQueueWait, MergeQueueWait.NONE),
    )
    #: How many times this entry was staged: 1 on its first staging, +1 per
    #: restage after the base moved, and per retry.
    attempts: int = 0
    #: The commit the required checks run on, its tree, and the base tip it was
    #: cut on. Empty until staged; cleared when a restage discards them.
    staged_sha: str = ""
    staged_tree: str = ""
    staged_base_sha: str = ""
    #: The last commit the queue pushed onto the PR head. Kept across a restage,
    #: so a PR admitted by number can tell its author's push from the queue's.
    pushed_sha: str = ""
    #: What landed: GitHub's merge commit and its tree, which must equal
    #: ``staged_tree``.
    merge_commit: str = ""
    landed_tree: str = ""
    #: Why the entry is where it is, in words for the operator.
    detail: str = ""
    conflicted_files_json: str = "[]"
    #: The last reading of the staged commit's required checks: a list of
    #: ``{"name", "outcome", "url"}``.
    checks_json: str = "[]"
    #: A failing check's log tail, kept because that is where it fails.
    log_tail: str = ""
    #: The durable thing a person resolves a conflict through (a ticket's
    #: external id), so a later cycle can find it.
    artifact_ref: str = ""
    enqueued_at: datetime = Field(default_factory=utcnow)
    state_changed_at: datetime = Field(default_factory=utcnow)
    #: When the current staged commit's wait began; the timeout counts from here.
    testing_started_at: datetime | None = None

    def conflicted_files(self) -> list[str]:
        return list(json.loads(self.conflicted_files_json))

    def checks(self) -> list[dict[str, str]]:
        return list(json.loads(self.checks_json))


__all__ = ["MergeQueueEntry"]
