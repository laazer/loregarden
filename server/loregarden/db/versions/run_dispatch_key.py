"""Each agent-run dispatch carries an idempotency key (`dispatch_key`).

``dispatch_key`` names the dispatch's lane and attempt; a partial unique index
makes two dispatches of one lane and attempt one row, while every older row,
which has none, stays out of it. ``execution_claimed_at`` records when a run
was first executed, so a run handed back to a replayed dispatch is not executed
a second time.

Nothing is backfilled: older runs were dispatched without a key, and inventing
one now would claim an attempt numbering nothing recorded.
"""

from __future__ import annotations

from loregarden.db.migration_utils import add_columns_if_missing, table_exists
from loregarden.db.versions import migration
from sqlalchemy import Connection, text


@migration("20261109_run_dispatch_key", after="20261108_ticket_deferral")
def m_run_dispatch_key(conn: Connection) -> None:
    # A database built up to an earlier id may have no `agent_runs` yet.
    if not table_exists(conn, "agent_runs"):
        return
    add_columns_if_missing(
        conn,
        "agent_runs",
        {
            "dispatch_key": "ALTER TABLE agent_runs ADD COLUMN dispatch_key TEXT NOT NULL DEFAULT ''",
            "execution_claimed_at": "ALTER TABLE agent_runs ADD COLUMN execution_claimed_at DATETIME",
        },
    )
    conn.execute(
        text(
            "CREATE UNIQUE INDEX IF NOT EXISTS ux_agent_runs_dispatch_key "
            "ON agent_runs (dispatch_key) WHERE dispatch_key != ''"
        )
    )
