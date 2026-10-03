"""Leases that take no lease-count slot: children, and agent runs' standing claims.

Until now a child's slotlessness was implied by `parent_lease_id`. Agent-run
leases need the same property without a parent, so it becomes a column, and the
existing children are back-filled to match what the ledger already assumed.
"""

from __future__ import annotations

from loregarden.db.migration_utils import add_columns_if_missing, table_exists
from loregarden.db.versions import migration
from sqlalchemy import Connection, text


@migration("20261003_capacity_lease_slots", after="20261002_capacity_child_leases")
def m_capacity_lease_slots(conn: Connection) -> None:
    if not table_exists(conn, "docker_leases"):
        return
    add_columns_if_missing(
        conn,
        "docker_leases",
        {
            "takes_slot": "ALTER TABLE docker_leases ADD COLUMN takes_slot BOOLEAN NOT NULL DEFAULT 1"
        },
    )
    conn.execute(text("UPDATE docker_leases SET takes_slot = 0 WHERE parent_lease_id IS NOT NULL"))
