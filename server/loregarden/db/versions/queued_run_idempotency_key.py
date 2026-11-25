"""A start's idempotency key on the lane entry it parks in.

A parked start has no run to carry the key, so it was dropped: the run the lane
started later could not be found by it. Empty on every existing row, which is
what they were filed with.
"""

from __future__ import annotations

from loregarden.db.migration_utils import add_columns_if_missing
from loregarden.db.versions import migration
from sqlalchemy import Connection


@migration("20261125_queued_run_idempotency_key", after="20261114_queued_run_held_block_kind")
def m_queued_run_idempotency_key(conn: Connection) -> None:
    add_columns_if_missing(
        conn,
        "queued_runs",
        {
            "idempotency_key": (
                "ALTER TABLE queued_runs ADD COLUMN idempotency_key TEXT NOT NULL DEFAULT ''"
            )
        },
    )
