"""Disk usage: the folders a person added to the scan, and what cleanup moved.

Re-exported from ``models.domain``. The scan itself is not a table: it is a
tree of tens of thousands of nodes rebuilt on every scan, so it lives as one
JSON file beside the database (`services.disk_usage.store`).
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from uuid import uuid4

from loregarden.models.domain.enums import str_enum_column, utcnow
from sqlmodel import Field, SQLModel


class DiskKind(StrEnum):
    """What a node in the disk tree is, as far as cleanup is concerned.

    The first three are regenerable by name: a package manager or a build puts
    them back. That is a hint for the picture, never the permission to delete:
    `services.disk_usage.safety` decides that on the path itself.
    """

    DEPENDENCIES = "dependencies"
    BUILD_OUTPUT = "build_output"
    CACHE = "cache"
    WORKTREE = "worktree"
    WORKSPACE = "workspace"
    GIT_STORE = "git_store"
    LOREGARDEN_DATA = "loregarden_data"
    DIRECTORY = "directory"
    FILE = "file"
    #: The many small entries folded into one tile so the tree stays readable.
    OTHER = "other"


REGENERABLE_KINDS: frozenset[DiskKind] = frozenset(
    {DiskKind.DEPENDENCIES, DiskKind.BUILD_OUTPUT, DiskKind.CACHE}
)


class DiskRootOrigin(StrEnum):
    """Why a folder is scanned."""

    WORKSPACE = "workspace"
    #: Worktrees of a registered workspace that live outside its checkout.
    WORKTREES = "worktrees"
    ADDED = "added"


class DiskCleanupMethod(StrEnum):
    TRASH = "trash"
    #: Moved to the Trash, then `git worktree prune` so the repository forgets it.
    TRASH_WORKTREE = "trash_worktree"


class DiskCleanupOutcome(StrEnum):
    TRASHED = "trashed"
    #: A safety check that passed at preview failed at execute.
    REFUSED = "refused"
    FAILED = "failed"


class DiskScanRoot(SQLModel, table=True):
    """A folder a person asked the disk scan to cover, beyond the workspaces."""

    __tablename__ = "disk_scan_roots"

    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True)
    path: str = Field(unique=True)
    added_at: datetime = Field(default_factory=utcnow)


class DiskCleanupEvent(SQLModel, table=True):
    """One path cleanup acted on, and what happened. The audit trail for the Trash."""

    __tablename__ = "disk_cleanup_events"

    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True)
    path: str
    size_bytes: int = 0
    kind: DiskKind = Field(sa_column=str_enum_column(DiskKind, DiskKind.DIRECTORY))
    method: DiskCleanupMethod = Field(
        sa_column=str_enum_column(DiskCleanupMethod, DiskCleanupMethod.TRASH)
    )
    outcome: DiskCleanupOutcome = Field(
        sa_column=str_enum_column(DiskCleanupOutcome, DiskCleanupOutcome.TRASHED)
    )
    detail: str = ""
    created_at: datetime = Field(default_factory=utcnow, index=True)


__all__ = [
    "REGENERABLE_KINDS",
    "DiskCleanupEvent",
    "DiskCleanupMethod",
    "DiskCleanupOutcome",
    "DiskKind",
    "DiskRootOrigin",
    "DiskScanRoot",
]
