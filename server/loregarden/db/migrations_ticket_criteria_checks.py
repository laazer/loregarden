"""Migration 0151: operators can tick off acceptance criteria."""

from __future__ import annotations

from loregarden.db.migration_utils import add_columns_if_missing
from sqlalchemy.engine import Connection


def m_ticket_criteria_checked(conn: Connection) -> None:
    """``checked_criteria_json``, a JSON array of criterion texts; nothing is checked yet."""
    add_columns_if_missing(
        conn,
        "tickets",
        {
            "checked_criteria_json": (
                "ALTER TABLE tickets ADD COLUMN checked_criteria_json TEXT NOT NULL DEFAULT '[]'"
            )
        },
    )
