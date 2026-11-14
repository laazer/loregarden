"""The kind of block a lane hold re-armed (`QueuedRun.held_block_kind`).

Re-arming a held stage clears `Ticket.block_kind`, so a hold that ended without
its repair had nothing to restore it from (lg-milestone-that-1420). Existing
rows keep null; `queue_repair_settle` classifies their block message instead.
"""

from __future__ import annotations

from loregarden.db.migration_utils import add_columns_if_missing, table_exists
from loregarden.db.versions import migration
from sqlalchemy import Connection


@migration("20261114_queued_run_held_block_kind", after="20261114_landing_lost_delivery_cleared")
def m_queued_run_held_block_kind(conn: Connection) -> None:
    if not table_exists(conn, "queued_runs"):
        # A partial schema (the migration suite builds them); create_all adds it whole.
        return
    add_columns_if_missing(
        conn,
        "queued_runs",
        {"held_block_kind": "ALTER TABLE queued_runs ADD COLUMN held_block_kind VARCHAR"},
    )
