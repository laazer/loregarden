"""A cutoff for the queue history rail, so Clear hides rather than deletes.

See `models.domain.queue_tables.QueueHistoryClear`. Idempotent: the table and
index are created only when missing, which is also what `create_all` leaves on
a fresh database.
"""

from __future__ import annotations

from loregarden.db.migration_utils import index_exists, table_exists
from loregarden.db.versions import migration
from sqlalchemy import Connection, text


@migration("20261005_queue_history_clears", after="20261003_capacity_unrun_holds")
def m_queue_history_clears(conn: Connection) -> None:
    if not table_exists(conn, "queue_history_clears"):
        conn.execute(
            text(
                "CREATE TABLE queue_history_clears ("
                "id VARCHAR NOT NULL PRIMARY KEY, "
                "cleared_at DATETIME NOT NULL)"
            )
        )
    if not index_exists(conn, "ix_queue_history_clears_cleared_at"):
        conn.execute(
            text(
                "CREATE INDEX ix_queue_history_clears_cleared_at "
                "ON queue_history_clears (cleared_at)"
            )
        )
