"""`auto_repair` on orchestrations and parked lane entries (750).

Default on: a harness/work block gets one repair turn under the run rather
than waiting for a person. Threads like `approve_design_plans` (0132).
"""

from __future__ import annotations

from loregarden.db.migration_utils import add_columns_if_missing, table_exists
from sqlalchemy.engine import Connection


def m_auto_repair_columns(conn: Connection) -> None:
    for table in ("orchestration_runs", "queued_runs"):
        if table_exists(conn, table):
            add_columns_if_missing(
                conn,
                table,
                {
                    "auto_repair": (
                        f"ALTER TABLE {table} ADD COLUMN auto_repair BOOLEAN NOT NULL DEFAULT 1"
                    )
                },
            )
