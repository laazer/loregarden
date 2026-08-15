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


def m_orchestration_run_lease(conn: Connection) -> None:
    """A liveness signal an absent owner cannot fake.

    `OrchestrationRun` carried no owner, pid, heartbeat or deadline, so
    `_occupant_is_live` had to answer "is this still running?" with
    `status in LIVE_ORCHESTRATION_STATUSES` — a field only the owner moves. An
    external harness that walked away therefore held its lane permanently, not
    until restart. `last_seen_at` is stamped by any control-plane write naming
    the run, so the lease is renewed by the work itself rather than by a human
    vouching for it.

    Null on existing rows and read as "never renewed", which falls back to the
    run's own start time — an abandoned run from before this migration is
    reclaimable on the first sweep rather than needing a backfill.
    """
    add_columns_if_missing(
        conn,
        "orchestration_runs",
        {
            "last_seen_at": "ALTER TABLE orchestration_runs ADD COLUMN last_seen_at TEXT",
        },
    )
