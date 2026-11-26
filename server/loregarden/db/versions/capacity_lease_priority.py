"""Why a waiting lease was put ahead of the line, and when.

Additive and guarded. An existing lease was never prioritized, which is what the
defaults — no reason, no time — say. The order itself needs no column: a
prioritized waiter's `position` is moved into a band below every normal one
(`docker_ledger.priority_position`), so every reader that already orders the
line by `position` agrees with the granter without learning a second key.
"""

from __future__ import annotations

from loregarden.db.migration_utils import add_columns_if_missing, table_exists
from loregarden.db.versions import migration
from sqlalchemy import Connection


@migration("20261125_capacity_lease_priority", after="20261125_queued_run_idempotency_key")
def m_capacity_lease_priority(conn: Connection) -> None:
    if not table_exists(conn, "docker_leases"):
        return
    add_columns_if_missing(
        conn,
        "docker_leases",
        {
            "priority_reason": (
                "ALTER TABLE docker_leases ADD COLUMN priority_reason TEXT NOT NULL DEFAULT ''"
            ),
            "prioritized_at": "ALTER TABLE docker_leases ADD COLUMN prioritized_at DATETIME",
        },
    )
