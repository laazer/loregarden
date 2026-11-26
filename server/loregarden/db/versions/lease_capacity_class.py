"""What kind of work a lease holds, and the repository it runs in.

Additive and guarded. Every existing lease is ordinary work with no repository,
which is what every lease was before sweeps had a class.
"""

from __future__ import annotations

from loregarden.db.migration_utils import add_columns_if_missing, table_exists
from loregarden.db.versions import migration
from sqlalchemy import Connection


@migration("20261125_lease_capacity_class", after="20261125_queued_run_idempotency_key")
def m_lease_capacity_class(conn: Connection) -> None:
    if not table_exists(conn, "docker_leases"):
        return
    add_columns_if_missing(
        conn,
        "docker_leases",
        {
            "capacity_class": (
                "ALTER TABLE docker_leases ADD COLUMN capacity_class "
                "VARCHAR NOT NULL DEFAULT 'ordinary'"
            ),
            "repo_key": "ALTER TABLE docker_leases ADD COLUMN repo_key TEXT NOT NULL DEFAULT ''",
        },
    )
