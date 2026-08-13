"""Schema for the doctor preflight a run records.

Its own module rather than another entry in ``migrations.py``, which sits four
lines under the organization gate's ceiling — see ``migrations_git_boundary`` for
the same reasoning applied to the boundary columns.
"""

from __future__ import annotations

from loregarden.db.migration_utils import add_columns_if_missing
from sqlalchemy.engine import Connection


def m_agent_run_preflight(conn: Connection) -> None:
    """Record which doctor checks failed before this run was dispatched.

    Only the failures, as a JSON array of check ids. Storing every result would
    write seven rows' worth of "fine" per dispatch to record the one case anybody
    queries, and the empty array is the answer for a healthy environment.
    """
    add_columns_if_missing(
        conn,
        "agent_runs",
        {
            "start_preflight_failures_json": (
                "ALTER TABLE agent_runs ADD COLUMN start_preflight_failures_json TEXT NOT NULL "
                "DEFAULT '[]'"
            ),
        },
    )
