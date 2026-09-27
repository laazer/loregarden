"""The `github_issue_links` table: a ticket's two-way link to a GitHub issue.

One row per linked ticket, carrying the issue's address and the merge base the
sync compares both sides against (see `models.domain.github_issue_tables`).
"""

from __future__ import annotations

from loregarden.db.migration_utils import add_columns_if_missing, index_exists, table_exists
from sqlalchemy import text
from sqlalchemy.engine import Connection


def m_github_issue_links(conn: Connection) -> None:
    if not table_exists(conn, "github_issue_links"):
        conn.execute(
            text(
                """
                CREATE TABLE github_issue_links (
                    id VARCHAR NOT NULL PRIMARY KEY,
                    ticket_id VARCHAR NOT NULL REFERENCES tickets(id),
                    workspace_id VARCHAR NOT NULL REFERENCES workspaces(id),
                    repo VARCHAR NOT NULL,
                    issue_number INTEGER NOT NULL,
                    issue_url VARCHAR NOT NULL DEFAULT '',
                    synced_title VARCHAR NOT NULL DEFAULT '',
                    synced_body VARCHAR NOT NULL DEFAULT '',
                    synced_closure VARCHAR(11) NOT NULL DEFAULT 'open',
                    last_synced_at DATETIME NOT NULL,
                    last_error VARCHAR NOT NULL DEFAULT '',
                    created_at DATETIME NOT NULL
                )
                """
            )
        )
    for name, ddl in (
        (
            "ix_github_issue_links_ticket_id",
            "CREATE UNIQUE INDEX ix_github_issue_links_ticket_id ON github_issue_links (ticket_id)",
        ),
        (
            "ix_github_issue_links_workspace_id",
            "CREATE INDEX ix_github_issue_links_workspace_id ON github_issue_links (workspace_id)",
        ),
        (
            "ix_github_issue_links_repo",
            "CREATE INDEX ix_github_issue_links_repo ON github_issue_links (repo)",
        ),
        (
            "ix_github_issue_links_issue_number",
            "CREATE INDEX ix_github_issue_links_issue_number ON github_issue_links (issue_number)",
        ),
    ):
        if not index_exists(conn, name):
            conn.execute(text(ddl))


def m_github_sync_settings(conn: Connection) -> None:
    """Per-workspace background sync settings. No row means off."""
    if table_exists(conn, "github_sync_settings"):
        return
    conn.execute(
        text(
            """
            CREATE TABLE github_sync_settings (
                workspace_id VARCHAR NOT NULL PRIMARY KEY REFERENCES workspaces(id),
                enabled BOOLEAN NOT NULL DEFAULT 0,
                interval_minutes INTEGER NOT NULL DEFAULT 15,
                import_parent_ticket_id VARCHAR NOT NULL DEFAULT '',
                import_label VARCHAR NOT NULL DEFAULT '',
                last_run_at DATETIME,
                last_error VARCHAR NOT NULL DEFAULT ''
            )
            """
        )
    )


def m_github_push_on_edit(conn: Connection) -> None:
    """Per-workspace switch: sync a linked ticket as soon as it is edited. Off."""
    add_columns_if_missing(
        conn,
        "github_sync_settings",
        {
            "push_on_edit": (
                "ALTER TABLE github_sync_settings "
                "ADD COLUMN push_on_edit BOOLEAN NOT NULL DEFAULT 0"
            )
        },
    )
