"""The host capacity pool: a second pool row, and the pool each lease claims from.

Additive and guarded. Existing leases were all docker claims, so the column
defaults to `'docker'`; the host row is seeded the way `0120` seeded the docker
row — every column named and `WHERE NOT EXISTS`, not `INSERT OR IGNORE`, which
would swallow a constraint violation and leave the pool missing.
"""

from __future__ import annotations

from loregarden.db.migration_utils import add_columns_if_missing, table_exists
from loregarden.db.versions import migration
from sqlalchemy import Connection, text


@migration("20261002_capacity_host_pool", after="0151_ticket_criteria_checked")
def m_capacity_host_pool(conn: Connection) -> None:
    if not table_exists(conn, "docker_capacity_pool") or not table_exists(conn, "docker_leases"):
        return
    add_columns_if_missing(
        conn,
        "docker_leases",
        {"pool": "ALTER TABLE docker_leases ADD COLUMN pool TEXT NOT NULL DEFAULT 'docker'"},
    )
    conn.execute(
        text(
            """
            INSERT INTO docker_capacity_pool (
                id, held_cpus, held_memory_mb, held_count,
                ceiling_cpus, ceiling_memory_mb, ceiling_leases,
                ceiling_source, probed_at, probe_error, revision, next_position
            )
            SELECT 'host', 0, 0, 0, 0, 0, 0, 'unknown', NULL, '', 0, 1
            WHERE NOT EXISTS (SELECT 1 FROM docker_capacity_pool WHERE id = 'host')
            """
        )
    )
    # Host totals start at zero, but every live docker lease is already charged
    # to the host under the new rule. Recompute from the ledger rather than
    # waiting a reap sweep, so admission never sees an under-counted host.
    conn.execute(
        text(
            """
            UPDATE docker_capacity_pool
            SET held_cpus = (SELECT COALESCE(SUM(cpus), 0) FROM docker_leases
                             WHERE status IN ('held', 'orphaned')),
                held_memory_mb = (SELECT COALESCE(SUM(memory_mb), 0) FROM docker_leases
                                  WHERE status IN ('held', 'orphaned')),
                held_count = (SELECT COUNT(*) FROM docker_leases
                              WHERE status IN ('held', 'orphaned'))
            WHERE id = 'host'
            """
        )
    )
