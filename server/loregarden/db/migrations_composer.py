"""Migrations for composer slash-commands and `@` references.

Two changes land together because one feature drives both: the composer's `/`
menu can pick a skill for the next turn, and its `/note` command writes a
post-it that outlives the tab it was typed in.

``baxter_chat_messages.skill_name`` records the skill the operator chose for
that turn. It sits on the user row rather than the assistant row because that is
the row the choice belongs to — the assistant turn is what the choice produced.

``composer_notes`` is workspace-scoped rather than session-scoped on purpose: a
note's whole point is that it survives the conversation it was written beside,
including "send this into a *new* chat".
"""

from __future__ import annotations

from loregarden.db.migration_utils import add_columns_if_missing, index_exists, table_exists
from sqlalchemy import text
from sqlalchemy.engine import Connection

_INDEXES = {
    "ix_composer_notes_workspace_id": (
        "CREATE INDEX ix_composer_notes_workspace_id ON composer_notes (workspace_id)"
    ),
    "ix_composer_notes_updated_at": (
        "CREATE INDEX ix_composer_notes_updated_at ON composer_notes (updated_at)"
    ),
}


def m_composer_commands(conn: Connection) -> None:
    add_columns_if_missing(
        conn,
        "baxter_chat_messages",
        {
            "skill_name": (
                "ALTER TABLE baxter_chat_messages ADD COLUMN skill_name TEXT NOT NULL DEFAULT ''"
            )
        },
    )

    if not table_exists(conn, "composer_notes"):
        conn.execute(
            text(
                """
                CREATE TABLE composer_notes (
                    id TEXT PRIMARY KEY,
                    workspace_id TEXT NOT NULL,
                    body TEXT NOT NULL DEFAULT '',
                    sent_at TEXT,
                    created_at TEXT NOT NULL DEFAULT (datetime('now')),
                    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
                    FOREIGN KEY(workspace_id) REFERENCES workspaces(id)
                )
                """
            )
        )

    for name, statement in _INDEXES.items():
        if not index_exists(conn, name):
            conn.execute(text(statement))
