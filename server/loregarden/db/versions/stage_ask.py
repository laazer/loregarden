"""The stage run that asked an earlier stage a question (`services.stage_ask`).

An ask is its own run: it resumes the earlier run's session (`resumed_from_run_id`)
on behalf of the run that asked (`asked_by_run_id`). Nothing to backfill: no
run asked before this column existed.
"""

from __future__ import annotations

from loregarden.db.migration_utils import add_columns_if_missing
from loregarden.db.versions import migration
from sqlalchemy import Connection

_COLUMNS = {
    "asked_by_run_id": "ALTER TABLE agent_runs ADD COLUMN asked_by_run_id TEXT NOT NULL DEFAULT ''"
}


@migration("20261119_stage_ask", after="20261119_next_stage_notes")
def m_stage_ask(conn: Connection) -> None:
    add_columns_if_missing(conn, "agent_runs", _COLUMNS)
