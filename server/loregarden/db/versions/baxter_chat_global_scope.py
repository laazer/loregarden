"""Global Home Baxter threads, optionally focused on one initiative.

See `models.domain.tables.BaxterChatSession.scope`. Every existing thread is
workspace-scoped, which the `'workspace'` default records; `initiative_id` is
NULL on all of them.
"""

from __future__ import annotations

from loregarden.db.migration_utils import add_columns_if_missing, index_exists
from loregarden.db.versions import migration
from sqlalchemy import Connection, text


@migration("20261010_baxter_chat_global_scope", after="20261008_agent_run_transport")
def m_baxter_chat_global_scope(conn: Connection) -> None:
    add_columns_if_missing(
        conn,
        "baxter_chat_sessions",
        {
            "scope": (
                "ALTER TABLE baxter_chat_sessions "
                "ADD COLUMN scope TEXT NOT NULL DEFAULT 'workspace'"
            ),
            "initiative_id": (
                "ALTER TABLE baxter_chat_sessions "
                "ADD COLUMN initiative_id TEXT REFERENCES tickets(id)"
            ),
        },
    )
    for name, column in (
        ("ix_baxter_chat_sessions_scope", "scope"),
        ("ix_baxter_chat_sessions_initiative_id", "initiative_id"),
    ):
        if not index_exists(conn, name):
            conn.execute(text(f"CREATE INDEX {name} ON baxter_chat_sessions ({column})"))
