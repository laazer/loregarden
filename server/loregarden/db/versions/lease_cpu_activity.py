"""When a held command's process tree was last sampled, and last used CPU.

Additive and guarded. Null on an existing lease: nothing measured it, which the
board reads as "no activity reading" rather than as idle.
"""

from __future__ import annotations

from loregarden.db.migration_utils import add_columns_if_missing, table_exists
from loregarden.db.versions import migration
from sqlalchemy import Connection


@migration("20261125_lease_cpu_activity", after="20261124_drop_worktree_conflicts")
def m_lease_cpu_activity(conn: Connection) -> None:
    if not table_exists(conn, "docker_leases"):
        return
    add_columns_if_missing(
        conn,
        "docker_leases",
        {
            "cpu_sampled_at": "ALTER TABLE docker_leases ADD COLUMN cpu_sampled_at DATETIME",
            "cpu_active_at": "ALTER TABLE docker_leases ADD COLUMN cpu_active_at DATETIME",
        },
    )
