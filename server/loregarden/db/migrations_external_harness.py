"""Schema for runs driven by a coding harness outside this control plane.

Split out of `migrations.py` rather than appended to it: that module is at the
organization gate's line limit, and the division there is by what a migration
touches. See `services/external_harness.py` for what reads these columns.
"""

from __future__ import annotations

from loregarden.db.migration_utils import add_columns_if_missing, index_exists, table_exists
from sqlalchemy import text
from sqlalchemy.engine import Connection


def m_external_harness_columns(conn: Connection) -> None:
    """Record which outside harness drove a run, when one did.

    Nullable on purpose: null means this control plane's own agents ran it, and
    a default of ``'other'`` would relabel every historical run as external.
    """
    add_columns_if_missing(
        conn,
        "orchestration_runs",
        {
            "external_harness": "ALTER TABLE orchestration_runs ADD COLUMN external_harness TEXT",
        },
    )
    add_columns_if_missing(
        conn,
        "agent_runs",
        {
            "external_harness": "ALTER TABLE agent_runs ADD COLUMN external_harness TEXT",
        },
    )
    if table_exists(conn, "orchestration_runs") and not index_exists(
        conn, "ix_orchestration_runs_external_harness"
    ):
        conn.execute(
            text(
                "CREATE INDEX ix_orchestration_runs_external_harness "
                "ON orchestration_runs (external_harness)"
            )
        )
    if table_exists(conn, "agent_runs") and not index_exists(
        conn, "ix_agent_runs_external_harness"
    ):
        conn.execute(
            text("CREATE INDEX ix_agent_runs_external_harness ON agent_runs (external_harness)")
        )
