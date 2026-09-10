"""Poll bookkeeping on a docker lease.

Its own migration rather than an edit to `docker_capacity_ledger`: that one is
in `SHIPPED_MIGRATION_IDS` and on a pushed branch, so a database that applied it
would never see a change to its body. Ids are append-only precisely so that
"already applied" and "up to date" cannot come apart — a new column gets a new
id even when the two land together.

Additive, so it is safe on a database that already carries leases: three columns
with defaults, and `add_columns_if_missing` skips any that exist.
"""

from __future__ import annotations

from loregarden.db.migration_utils import add_columns_if_missing, table_exists
from sqlalchemy.engine import Connection


def m_docker_lease_polling(conn: Connection) -> None:
    """Record when a waiter last asked, so a tight poll loop can be told to slow down."""
    if not table_exists(conn, "docker_leases"):
        return
    add_columns_if_missing(
        conn,
        "docker_leases",
        {
            "last_polled_at": "ALTER TABLE docker_leases ADD COLUMN last_polled_at DATETIME",
            "poll_count": (
                "ALTER TABLE docker_leases ADD COLUMN poll_count INTEGER NOT NULL DEFAULT 0"
            ),
            "throttled_poll_count": (
                "ALTER TABLE docker_leases ADD COLUMN throttled_poll_count "
                "INTEGER NOT NULL DEFAULT 0"
            ),
        },
    )
