"""Chat waits: one outstanding background operation per chat thread, plus Home run_id."""

from __future__ import annotations

from loregarden.db.migration_utils import add_columns_if_missing, index_exists, table_exists
from loregarden.db.versions import migration
from sqlalchemy import Connection, text


@migration("20261027_chat_waits", after="20261023_disk_usage")
def m_chat_waits(conn: Connection) -> None:
    if not table_exists(conn, "chat_waits"):
        conn.execute(
            text(
                "CREATE TABLE chat_waits ("
                "id VARCHAR NOT NULL PRIMARY KEY, "
                "surface VARCHAR NOT NULL, "
                "thread_key VARCHAR NOT NULL, "
                "origin_run_id VARCHAR NOT NULL, "
                "pid INTEGER NOT NULL, "
                "pid_identity VARCHAR NOT NULL, "
                "command VARCHAR NOT NULL, "
                "log_path VARCHAR NOT NULL, "
                "registered_at DATETIME NOT NULL, "
                "deadline DATETIME NOT NULL, "
                "status VARCHAR NOT NULL, "
                "continued_index INTEGER, "
                "FOREIGN KEY(origin_run_id) REFERENCES agent_runs (id))"
            )
        )
    if not index_exists(conn, "ix_chat_waits_thread_key"):
        conn.execute(text("CREATE INDEX ix_chat_waits_thread_key ON chat_waits (thread_key)"))
    if not index_exists(conn, "ix_chat_waits_origin_run_id"):
        conn.execute(text("CREATE INDEX ix_chat_waits_origin_run_id ON chat_waits (origin_run_id)"))
    if not index_exists(conn, "ix_chat_waits_status"):
        conn.execute(text("CREATE INDEX ix_chat_waits_status ON chat_waits (status)"))
    if not index_exists(conn, "ux_chat_waits_outstanding_thread"):
        conn.execute(
            text(
                "CREATE UNIQUE INDEX ux_chat_waits_outstanding_thread "
                "ON chat_waits (surface, thread_key) WHERE status = 'outstanding'"
            )
        )
    add_columns_if_missing(
        conn,
        "baxter_chat_messages",
        {
            "run_id": (
                "ALTER TABLE baxter_chat_messages "
                "ADD COLUMN run_id VARCHAR REFERENCES agent_runs (id)"
            )
        },
    )
    if not index_exists(conn, "ix_baxter_chat_messages_run_id"):
        conn.execute(
            text("CREATE INDEX ix_baxter_chat_messages_run_id ON baxter_chat_messages (run_id)")
        )
