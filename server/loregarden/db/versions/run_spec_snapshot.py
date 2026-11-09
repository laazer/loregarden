"""Each run records the spec it was given at dispatch (`spec_snapshot`).

Nothing is backfilled: what an earlier run's prompt carried was never recorded,
and re-hashing today's ticket would claim a spec the run may never have seen.
Old runs keep "" and read as no record.
"""

from __future__ import annotations

from loregarden.db.migration_utils import add_columns_if_missing
from loregarden.db.versions import migration
from sqlalchemy import Connection

_COLUMNS = {
    name: f"ALTER TABLE agent_runs ADD COLUMN {name} TEXT NOT NULL DEFAULT ''"
    for name in ("spec_snapshot_json", "spec_hash")
}


@migration("20261109_run_spec_snapshot", after="20261108_ticket_deferral")
def m_run_spec_snapshot(conn: Connection) -> None:
    add_columns_if_missing(conn, "agent_runs", _COLUMNS)
