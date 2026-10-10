"""A configurable lane count, replacing the literal 3 in every queue service.

See `models.domain.queue_tables.QueueSettings`. No row is written: a missing
row reads as the default, which is exactly what every database ran with before.
"""

from __future__ import annotations

from loregarden.db.migration_utils import table_exists
from loregarden.db.versions import migration
from sqlalchemy import Connection, text


@migration("20261005_queue_lane_count", after="20261005_queue_history_clears")
def m_queue_lane_count(conn: Connection) -> None:
    if table_exists(conn, "queue_settings"):
        return
    conn.execute(
        text(
            "CREATE TABLE queue_settings ("
            "id VARCHAR NOT NULL PRIMARY KEY, "
            "lane_count INTEGER NOT NULL DEFAULT 3, "
            "updated_at DATETIME NOT NULL)"
        )
    )
