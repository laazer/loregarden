"""A chat's focus set and its feature loop.

`baxter_chat_focus` holds the workspaces and initiatives a thread works across;
`feature_criteria` holds a feature loop's acceptance criteria, and
`baxter_chat_sessions.feature_ticket_id` the ticket the loop builds. See
`models.domain.chat_tables`. Every existing thread has no focus and no loop.
"""

from __future__ import annotations

from loregarden.db.migration_utils import add_columns_if_missing, index_exists, table_exists
from loregarden.db.versions import migration
from sqlalchemy import Connection, text


def _index(conn: Connection, table: str, column: str) -> None:
    name = f"ix_{table}_{column}"
    if not index_exists(conn, name):
        conn.execute(text(f"CREATE INDEX {name} ON {table} ({column})"))


@migration("20261023_baxter_chat_focus_and_feature_loop", after="20261023_surveyor_discovery_runs")
def m_baxter_chat_focus_and_feature_loop(conn: Connection) -> None:
    if not table_exists(conn, "baxter_chat_focus"):
        conn.execute(
            text(
                "CREATE TABLE baxter_chat_focus ( id VARCHAR NOT NULL, session_id VARCHAR NOT NULL, "
                "kind VARCHAR(10) NOT NULL, ref_id VARCHAR NOT NULL, created_at DATETIME NOT NULL, "
                "PRIMARY KEY (id), UNIQUE (session_id, kind, ref_id), "
                "FOREIGN KEY(session_id) REFERENCES baxter_chat_sessions (id) )"
            )
        )
    _index(conn, "baxter_chat_focus", "session_id")
    if not table_exists(conn, "feature_criteria"):
        conn.execute(
            text(
                "CREATE TABLE feature_criteria ( id VARCHAR NOT NULL, session_id VARCHAR NOT NULL, "
                "ticket_id VARCHAR NOT NULL, text VARCHAR NOT NULL, status VARCHAR(8) NOT NULL, "
                "round INTEGER NOT NULL, created_at DATETIME NOT NULL, decided_at DATETIME, "
                "PRIMARY KEY (id), "
                "FOREIGN KEY(session_id) REFERENCES baxter_chat_sessions (id), "
                "FOREIGN KEY(ticket_id) REFERENCES tickets (id) )"
            )
        )
    for column in ("session_id", "ticket_id", "status"):
        _index(conn, "feature_criteria", column)
    add_columns_if_missing(
        conn,
        "baxter_chat_sessions",
        {
            "feature_ticket_id": (
                "ALTER TABLE baxter_chat_sessions "
                "ADD COLUMN feature_ticket_id VARCHAR REFERENCES tickets (id)"
            )
        },
    )
    _index(conn, "baxter_chat_sessions", "feature_ticket_id")
