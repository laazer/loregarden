"""Persist continuation exit_code + log tail on the wait before schedule."""

from __future__ import annotations

from loregarden.db.migration_utils import add_columns_if_missing, index_exists, table_exists
from loregarden.db.versions import migration
from sqlalchemy import Connection, text


@migration("20261027_chat_wait_payload", after="20261027_chat_waits")
def m_chat_wait_payload(conn: Connection) -> None:
    if not table_exists(conn, "chat_waits"):
        return
    add_columns_if_missing(
        conn,
        "chat_waits",
        {
            "exit_code": "ALTER TABLE chat_waits ADD COLUMN exit_code INTEGER",
            "log_tail": "ALTER TABLE chat_waits ADD COLUMN log_tail TEXT",
            "continuation_run_id": (
                "ALTER TABLE chat_waits ADD COLUMN continuation_run_id VARCHAR "
                "REFERENCES agent_runs (id)"
            ),
        },
    )
    if not index_exists(conn, "ix_chat_waits_continuation_run_id"):
        conn.execute(
            text(
                "CREATE INDEX ix_chat_waits_continuation_run_id ON chat_waits (continuation_run_id)"
            )
        )
