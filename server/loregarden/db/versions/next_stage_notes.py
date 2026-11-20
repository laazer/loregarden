"""A stage run's notes for the stages after it (`agents.stage_digest`).

Nothing is backfilled: no earlier report carried the field. "" on old runs, and
the digest then shows only the files they changed and read.
"""

from __future__ import annotations

from loregarden.db.migration_utils import add_columns_if_missing
from loregarden.db.versions import migration
from sqlalchemy import Connection

_COLUMNS = {
    "next_stage_notes": "ALTER TABLE agent_runs ADD COLUMN next_stage_notes TEXT NOT NULL DEFAULT ''"
}


@migration("20261119_next_stage_notes", after="20261119_merge_plan_and_test_lanes")
def m_next_stage_notes(conn: Connection) -> None:
    add_columns_if_missing(conn, "agent_runs", _COLUMNS)
