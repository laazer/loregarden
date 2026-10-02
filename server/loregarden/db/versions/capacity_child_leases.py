"""Parent/child capacity leases: what a child drew from its parent's grant.

Additive and guarded. Every existing lease is a top-level claim, so the defaults
— no parent, nothing covered, nothing drawn — describe them exactly.
"""

from __future__ import annotations

from loregarden.db.migration_utils import add_columns_if_missing, table_exists
from loregarden.db.versions import migration
from sqlalchemy import Connection, text


@migration("20261002_capacity_child_leases", after="20261002_capacity_host_pool")
def m_capacity_child_leases(conn: Connection) -> None:
    if not table_exists(conn, "docker_leases"):
        return
    add_columns_if_missing(
        conn,
        "docker_leases",
        {
            "parent_lease_id": (
                "ALTER TABLE docker_leases ADD COLUMN parent_lease_id TEXT "
                "REFERENCES docker_leases(id)"
            ),
            "covered_cpus": (
                "ALTER TABLE docker_leases ADD COLUMN covered_cpus FLOAT NOT NULL DEFAULT 0"
            ),
            "covered_memory_mb": (
                "ALTER TABLE docker_leases ADD COLUMN covered_memory_mb INTEGER NOT NULL DEFAULT 0"
            ),
            "child_covered_cpus": (
                "ALTER TABLE docker_leases ADD COLUMN child_covered_cpus FLOAT NOT NULL DEFAULT 0"
            ),
            "child_covered_memory_mb": (
                "ALTER TABLE docker_leases ADD COLUMN child_covered_memory_mb "
                "INTEGER NOT NULL DEFAULT 0"
            ),
        },
    )
    conn.execute(
        text(
            "CREATE INDEX IF NOT EXISTS ix_docker_leases_parent_lease_id "
            "ON docker_leases (parent_lease_id)"
        )
    )
