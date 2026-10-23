"""Disk usage: folders added to the scan, and the audit trail of what cleanup moved.

See `models.domain.disk_tables`. Both tables start empty: the scan covers every
workspace without a row, and nothing has been cleaned yet.
"""

from __future__ import annotations

from loregarden.db.migration_utils import table_exists
from loregarden.db.versions import migration
from sqlalchemy import Connection, text


@migration("20261023_disk_usage", after="20261023_surveyor_discovery_runs")
def m_disk_usage(conn: Connection) -> None:
    if not table_exists(conn, "disk_scan_roots"):
        conn.execute(
            text(
                "CREATE TABLE disk_scan_roots ("
                "id VARCHAR NOT NULL PRIMARY KEY, "
                "path VARCHAR NOT NULL UNIQUE, "
                "added_at DATETIME NOT NULL)"
            )
        )
    if not table_exists(conn, "disk_cleanup_events"):
        conn.execute(
            text(
                "CREATE TABLE disk_cleanup_events ("
                "id VARCHAR NOT NULL PRIMARY KEY, "
                "path VARCHAR NOT NULL, "
                "size_bytes INTEGER NOT NULL DEFAULT 0, "
                "kind VARCHAR NOT NULL, "
                "method VARCHAR NOT NULL, "
                "outcome VARCHAR NOT NULL, "
                "detail VARCHAR NOT NULL DEFAULT '', "
                "created_at DATETIME NOT NULL)"
            )
        )
        conn.execute(
            text(
                "CREATE INDEX ix_disk_cleanup_events_created_at ON disk_cleanup_events (created_at)"
            )
        )
