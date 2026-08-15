"""Migrations for the chat surfaces — Home Baxter, and the live turn transcript.

Grouped out of ``migrations.py`` because they are one story: a conversation that
survives a reload, the ordered parts its messages are rendered from, and the
thinking/answer stream a turn publishes while it is still running.
"""

from __future__ import annotations

from loregarden.db.migration_utils import add_columns_if_missing, table_exists
from sqlalchemy import text
from sqlalchemy.engine import Connection


def m_chat_message_parts(conn: Connection) -> None:
    """Persist ordered ChatPart JSON on stored chat messages."""
    add_columns_if_missing(
        conn,
        "branch_triage_messages",
        {
            "parts_json": (
                "ALTER TABLE branch_triage_messages ADD COLUMN parts_json "
                "TEXT NOT NULL DEFAULT '[]'"
            ),
        },
    )
    add_columns_if_missing(
        conn,
        "ticket_studio_messages",
        {
            "parts_json": (
                "ALTER TABLE ticket_studio_messages ADD COLUMN parts_json "
                "TEXT NOT NULL DEFAULT '[]'"
            ),
        },
    )


def m_baxter_chat_tables(conn: Connection) -> None:
    """Persist Home Baxter conversations as named sessions.

    Home chat previously lived only in React state and replayed its history from
    the client each turn, so a reload lost the thread and the archive had nothing
    to list. ``triage_messages`` gains the ``parts_json`` its siblings got in
    0048 so ticket triage primitives survive a reload too.
    """
    if not table_exists(conn, "baxter_chat_sessions"):
        conn.execute(
            text(
                """
                CREATE TABLE baxter_chat_sessions (
                    id TEXT PRIMARY KEY,
                    workspace_id TEXT NOT NULL,
                    title TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL DEFAULT (datetime('now')),
                    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
                    FOREIGN KEY(workspace_id) REFERENCES workspaces(id)
                )
                """
            )
        )
        conn.execute(
            text(
                "CREATE INDEX ix_baxter_chat_sessions_workspace_updated "
                "ON baxter_chat_sessions (workspace_id, updated_at)"
            )
        )
    if not table_exists(conn, "baxter_chat_messages"):
        conn.execute(
            text(
                """
                CREATE TABLE baxter_chat_messages (
                    id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT 'complete',
                    parts_json TEXT NOT NULL DEFAULT '[]',
                    created_at TEXT NOT NULL DEFAULT (datetime('now')),
                    FOREIGN KEY(session_id) REFERENCES baxter_chat_sessions(id)
                )
                """
            )
        )
        conn.execute(
            text(
                "CREATE INDEX ix_baxter_chat_messages_session_created "
                "ON baxter_chat_messages (session_id, created_at)"
            )
        )
    add_columns_if_missing(
        conn,
        "triage_messages",
        {
            "parts_json": (
                "ALTER TABLE triage_messages ADD COLUMN parts_json TEXT NOT NULL DEFAULT '[]'"
            ),
        },
    )


def m_chat_turn_thinking(conn: Connection) -> None:
    """A place to keep a chat turn's reasoning while the turn is still running.

    Keyed by the ``active_turn_id`` every chat surface already publishes, so one
    table covers Home chat, branch triage, ticket triage and the studio without
    a column on each of their four message tables. Rows are deleted as their
    turn settles — the transcript is folded into the message's ``parts_json``
    then — so this table is empty whenever nothing is running.
    """
    if table_exists(conn, "chat_turn_thinking"):
        return
    conn.execute(
        text(
            """
            CREATE TABLE chat_turn_thinking (
                turn_id TEXT PRIMARY KEY,
                content TEXT NOT NULL DEFAULT '',
                activity TEXT NOT NULL DEFAULT '',
                seq INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL DEFAULT (datetime('now'))
            )
            """
        )
    )
    conn.execute(
        text("CREATE INDEX ix_chat_turn_thinking_updated ON chat_turn_thinking (updated_at)")
    )


def m_chat_turn_answer(conn: Connection) -> None:
    """Stream the reply as well as the reasoning.

    A read-only turn — the advisory chat fallbacks and every Ticket Studio
    scoper turn — emits an empty thinking block: the reply text is the only
    thing that actually streams. Without somewhere to put it those surfaces get
    a live panel with nothing in it.
    """
    add_columns_if_missing(
        conn,
        "chat_turn_thinking",
        {"answer": "ALTER TABLE chat_turn_thinking ADD COLUMN answer TEXT NOT NULL DEFAULT ''"},
    )


def m_baxter_chat_runtime(conn: Connection) -> None:
    add_columns_if_missing(
        conn,
        "baxter_chat_sessions",
        {
            "runtime_json": (
                "ALTER TABLE baxter_chat_sessions ADD COLUMN runtime_json "
                "TEXT NOT NULL DEFAULT '{}'"
            ),
        },
    )
