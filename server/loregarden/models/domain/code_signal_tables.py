"""Where the behavioural git signals are stored (lg-code-knowledge-299).

`services.code_signals.store` is the only writer, and it replaces a
workspace's rows wholesale rather than upserting them, so a path that leaves
the window leaves the table with it.

Every signal is its own typed scalar column: no JSON blob, no delimited set.
`window_days`, `head_sha` and `computed_at` are the staleness stamp the
consumer tickets read — they say what the row measured, not what it measured
it to be.

Co-change pairs are stored in BOTH directions (plan D4). It doubles a table
that is small anyway, and it makes "the partners of this file" one index scan
with no OR and no UNION pushed onto every consumer.
"""

from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from loregarden.models.domain.enums import utcnow
from sqlalchemy import Index, UniqueConstraint
from sqlmodel import Field, SQLModel


class FileGitSignal(SQLModel, table=True):
    """One row per (workspace, path): everything the window says about it."""

    __tablename__ = "file_git_signals"
    __table_args__ = (
        # Read pattern A: one path's signals, one seek.
        UniqueConstraint("workspace_id", "file_path", name="uq_file_git_signals_path"),
        # Read pattern B: the top N by each of the two ranked signals.
        Index("ix_file_git_signals_churn", "workspace_id", "churn_commits"),
        Index("ix_file_git_signals_bugfix", "workspace_id", "bugfix_commits"),
    )

    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True)
    workspace_id: str = Field(foreign_key="workspaces.id")
    file_path: str
    #: Commits in the window that touched this path, binary ones included.
    churn_commits: int = 0
    churn_added: int = 0
    churn_deleted: int = 0
    #: Naive UTC, as the ORM stores every timestamp here. The value object the
    #: computation returns is aware UTC; `store` converts at the boundary.
    last_commit_at: datetime
    #: The author email with the most ADDED lines, ties broken by the
    #: lexicographically smallest email. Empty when nobody added a line.
    top_author: str = ""
    #: That author's added lines over the path's total. Authored-line share,
    #: NOT surviving-line share — the name says so because `git blame` is what
    #: would answer the other question, and it is not what produced this.
    top_author_added_share: float = 0.0
    distinct_authors: int = 0
    #: Fix commits that touched this path AS A SOURCE PATH.
    bugfix_commits: int = 0
    last_bugfix_at: datetime | None = None
    #: The staleness stamp: what window this row measured, from which commit,
    #: when. A row stamped with the default window after a non-default run
    #: would be a lie about what it measured.
    window_days: int
    head_sha: str = ""
    computed_at: datetime = Field(default_factory=utcnow)


class FileCoChangePair(SQLModel, table=True):
    """How often two paths changed in the same commit, in both directions."""

    __tablename__ = "file_co_change_pairs"
    __table_args__ = (
        UniqueConstraint("workspace_id", "file_path", "partner_path", name="uq_file_co_change"),
        # "Partners of this file, strongest first" with no OR and no UNION.
        Index("ix_file_co_change_support", "workspace_id", "file_path", "support"),
    )

    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True)
    workspace_id: str = Field(foreign_key="workspaces.id")
    file_path: str
    partner_path: str
    #: Commits in the window that touched both paths.
    support: int = 0
    computed_at: datetime = Field(default_factory=utcnow)


__all__ = ["FileCoChangePair", "FileGitSignal"]
