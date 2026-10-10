"""How far a held command has got: the step and test count it last reported.

Additive and guarded. An existing lease reported nothing, which is what the
defaults — no step, no counts — say.
"""

from __future__ import annotations

from loregarden.db.migration_utils import add_columns_if_missing, table_exists
from loregarden.db.versions import migration
from sqlalchemy import Connection


@migration("20261009_capacity_lease_progress", after="20261009_motion_guide")
def m_capacity_lease_progress(conn: Connection) -> None:
    if not table_exists(conn, "docker_leases"):
        return
    add_columns_if_missing(
        conn,
        "docker_leases",
        {
            "progress_step": (
                "ALTER TABLE docker_leases ADD COLUMN progress_step TEXT NOT NULL DEFAULT ''"
            ),
            "progress_done": "ALTER TABLE docker_leases ADD COLUMN progress_done INTEGER",
            "progress_total": "ALTER TABLE docker_leases ADD COLUMN progress_total INTEGER",
            "progress_at": "ALTER TABLE docker_leases ADD COLUMN progress_at DATETIME",
        },
    )
