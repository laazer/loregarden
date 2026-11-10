"""`docker_capacity_pool.external_cpus`: busy cores the ledger does not hold.

Measured beside the host ceiling (`services/host_capacity.external_cpus`) and
read by the drain, so a claim is judged against what the machine has idle and
not only against its size. Additive and guarded: an existing row reads 0, which
is "no outside load recorded" and holds nothing back until the next measurement
writes a real figure.
"""

from __future__ import annotations

from loregarden.db.migration_utils import add_columns_if_missing, table_exists
from loregarden.db.versions import migration
from sqlalchemy import Connection


@migration("20261109_capacity_host_external_load", after="20261109_run_spec_snapshot")
def m_capacity_host_external_load(conn: Connection) -> None:
    if not table_exists(conn, "docker_capacity_pool"):
        return
    add_columns_if_missing(
        conn,
        "docker_capacity_pool",
        {
            "external_cpus": (
                "ALTER TABLE docker_capacity_pool ADD COLUMN external_cpus REAL NOT NULL DEFAULT 0"
            )
        },
    )
