"""Migration 0150: workspaces can be archived."""

from __future__ import annotations

from loregarden.db.migration_utils import add_columns_if_missing
from sqlalchemy.engine import Connection


def m_workspace_archived_at(conn: Connection) -> None:
    """Nullable ``archived_at``; every existing workspace stays active."""
    add_columns_if_missing(
        conn,
        "workspaces",
        {"archived_at": "ALTER TABLE workspaces ADD COLUMN archived_at DATETIME"},
    )
