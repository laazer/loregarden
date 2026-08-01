"""Migrations for the ticket studio's own tables.

Split out of `migrations.py` for the organization gate's file-size limit, on the
same principle as `migrations_templates.py`: a cluster, not an arbitrary cut.
These four all reshape `ticket_studio_sessions` / `ticket_studio_messages` —
including the reference-repo columns, which hang off a studio session.

Migration identity is the id string in the MIGRATIONS list, which does not move
with the body, so applied history is unaffected.
"""

from __future__ import annotations

from loregarden.db.migration_utils import add_columns_if_missing, table_exists
from sqlalchemy import text
from sqlalchemy.engine import Connection


def m_ticket_studio_tables(conn: Connection) -> None:
    if not table_exists(conn, "ticket_studio_sessions"):
        conn.execute(
            text(
                """
                CREATE TABLE ticket_studio_sessions (
                    id TEXT PRIMARY KEY,
                    workspace_id TEXT NOT NULL,
                    title TEXT NOT NULL,
                    brief TEXT NOT NULL DEFAULT '',
                    parent_ticket_id TEXT,
                    status TEXT NOT NULL DEFAULT 'draft',
                    draft_json TEXT NOT NULL DEFAULT '[]',
                    summary TEXT NOT NULL DEFAULT '',
                    clarifying_questions_json TEXT NOT NULL DEFAULT '[]',
                    clarifying_answers_json TEXT NOT NULL DEFAULT '[]',
                    runtime_json TEXT NOT NULL DEFAULT '{}',
                    is_preview INTEGER NOT NULL DEFAULT 0,
                    imported_tickets_json TEXT NOT NULL DEFAULT '[]',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(workspace_id) REFERENCES workspaces(id),
                    FOREIGN KEY(parent_ticket_id) REFERENCES tickets(id)
                )
                """
            )
        )
        conn.execute(
            text(
                "CREATE INDEX ix_ticket_studio_sessions_workspace_id "
                "ON ticket_studio_sessions (workspace_id)"
            )
        )
        conn.execute(
            text("CREATE INDEX ix_ticket_studio_sessions_status ON ticket_studio_sessions (status)")
        )
    else:
        add_columns_if_missing(
            conn,
            "ticket_studio_sessions",
            {
                "clarifying_answers_json": (
                    "ALTER TABLE ticket_studio_sessions "
                    "ADD COLUMN clarifying_answers_json TEXT NOT NULL DEFAULT '[]'"
                ),
            },
        )

    if not table_exists(conn, "ticket_studio_messages"):
        conn.execute(
            text(
                """
                CREATE TABLE ticket_studio_messages (
                    id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(session_id) REFERENCES ticket_studio_sessions(id)
                )
                """
            )
        )
        conn.execute(
            text(
                "CREATE INDEX ix_ticket_studio_messages_session_id "
                "ON ticket_studio_messages (session_id)"
            )
        )


def m_ticket_studio_preview_state(conn: Connection) -> None:
    add_columns_if_missing(
        conn,
        "ticket_studio_sessions",
        {
            "is_preview": (
                "ALTER TABLE ticket_studio_sessions "
                "ADD COLUMN is_preview INTEGER NOT NULL DEFAULT 0"
            ),
            "imported_tickets_json": (
                "ALTER TABLE ticket_studio_sessions "
                "ADD COLUMN imported_tickets_json TEXT NOT NULL DEFAULT '[]'"
            ),
        },
    )


def m_ticket_studio_turn_lifecycle(conn: Connection) -> None:
    """Give Ticket Studio turns the durable lifecycle the other chats have.

    The scoper ran its model call on the request thread, so a restart or a
    dropped connection lost the turn with no record it had ever started.
    ``status`` makes an in-flight turn recoverable; ``turn_mode`` records which
    kind of turn it is, because the reply is applied to the session differently
    per mode and the worker settling it did not start it.
    """
    add_columns_if_missing(
        conn,
        "ticket_studio_messages",
        {
            "status": (
                "ALTER TABLE ticket_studio_messages ADD COLUMN status "
                "TEXT NOT NULL DEFAULT 'complete'"
            ),
            "turn_mode": (
                "ALTER TABLE ticket_studio_messages ADD COLUMN turn_mode TEXT NOT NULL DEFAULT ''"
            ),
        },
    )


def m_reference_repos(conn: Connection) -> None:
    """Workspace-scoped third-party checkouts the ticket studio scoper reads from,
    plus the ticket studio session columns that attach them and hold the survey the
    scoper produced. Created empty; existing sessions default to no repos, no survey."""
    conn.execute(
        text(
            "CREATE TABLE IF NOT EXISTS reference_repos ("
            "id TEXT PRIMARY KEY, "
            "workspace_id TEXT NOT NULL, "
            "url TEXT NOT NULL DEFAULT '', "
            "slug TEXT NOT NULL DEFAULT '', "
            "name TEXT NOT NULL DEFAULT '', "
            "local_path TEXT NOT NULL DEFAULT '', "
            "default_branch TEXT NOT NULL DEFAULT '', "
            "head_sha TEXT NOT NULL DEFAULT '', "
            "notes TEXT NOT NULL DEFAULT '', "
            "last_synced_at TEXT, "
            "created_at TEXT NOT NULL, "
            "updated_at TEXT NOT NULL"
            ")"
        )
    )
    conn.execute(
        text(
            "CREATE INDEX IF NOT EXISTS ix_reference_repos_workspace_id "
            "ON reference_repos (workspace_id)"
        )
    )
    conn.execute(
        text("CREATE INDEX IF NOT EXISTS ix_reference_repos_slug ON reference_repos (slug)")
    )
    add_columns_if_missing(
        conn,
        "ticket_studio_sessions",
        {
            "reference_repo_ids_json": (
                "ALTER TABLE ticket_studio_sessions ADD COLUMN "
                "reference_repo_ids_json TEXT NOT NULL DEFAULT '[]'"
            ),
            "survey_json": (
                "ALTER TABLE ticket_studio_sessions ADD COLUMN "
                "survey_json TEXT NOT NULL DEFAULT '[]'"
            ),
        },
    )
