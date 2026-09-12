"""Migrations for the two diff-review comment tables.

Both create the same shape of anchored review comment — file, line, kind, body,
resolved — against a different subject: one ticket's diff, and one branch's.
Grouped out of ``migrations.py`` for that reason, and because that module had
reached the organization gate's 1500-line cap.
"""

from __future__ import annotations

from loregarden.db.migration_utils import table_exists
from sqlalchemy import text
from sqlalchemy.engine import Connection


def m_ticket_diff_comments(conn: Connection) -> None:
    if table_exists(conn, "ticket_diff_comments"):
        return
    conn.execute(
        text(
            """
            CREATE TABLE ticket_diff_comments (
                id TEXT PRIMARY KEY,
                ticket_id TEXT NOT NULL,
                file_path TEXT NOT NULL,
                line_index INTEGER NOT NULL,
                line_kind TEXT NOT NULL DEFAULT 'c',
                content TEXT NOT NULL,
                resolved INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                created_by TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL DEFAULT (datetime('now')),
                FOREIGN KEY(ticket_id) REFERENCES tickets(id)
            )
            """
        )
    )
    conn.execute(
        text("CREATE INDEX ix_ticket_diff_comments_ticket_id ON ticket_diff_comments (ticket_id)")
    )
    conn.execute(
        text(
            "CREATE INDEX ix_ticket_diff_comments_anchor "
            "ON ticket_diff_comments (ticket_id, file_path, line_index)"
        )
    )


def m_branch_diff_comments(conn: Connection) -> None:
    if table_exists(conn, "branch_diff_comments"):
        return
    conn.execute(
        text(
            """
            CREATE TABLE branch_diff_comments (
                id TEXT PRIMARY KEY,
                workspace_id TEXT NOT NULL,
                branch TEXT NOT NULL,
                file_path TEXT NOT NULL,
                line_index INTEGER NOT NULL,
                line_kind TEXT NOT NULL DEFAULT 'c',
                content TEXT NOT NULL,
                resolved INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                created_by TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL DEFAULT (datetime('now')),
                FOREIGN KEY(workspace_id) REFERENCES workspaces(id)
            )
            """
        )
    )
    conn.execute(
        text(
            "CREATE INDEX ix_branch_diff_comments_workspace_branch "
            "ON branch_diff_comments (workspace_id, branch)"
        )
    )
    conn.execute(
        text(
            "CREATE INDEX ix_branch_diff_comments_anchor "
            "ON branch_diff_comments (workspace_id, branch, file_path, line_index)"
        )
    )
