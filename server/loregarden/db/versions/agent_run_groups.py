"""A run records the agent group it dispatched and the steps it covered (`agent_groups`).

Nothing is backfilled: no run before this one was a group run, so every old
run reads as ungrouped (`agent_group_key == ""`, `covered_stage_keys_json == "[]"`).
"""

from __future__ import annotations

from loregarden.db.migration_utils import add_columns_if_missing
from loregarden.db.versions import migration
from sqlalchemy import Connection

_COLUMNS = {
    "agent_group_key": "ALTER TABLE agent_runs ADD COLUMN agent_group_key TEXT NOT NULL DEFAULT ''",
    "covered_stage_keys_json": (
        "ALTER TABLE agent_runs ADD COLUMN covered_stage_keys_json TEXT NOT NULL DEFAULT '[]'"
    ),
}


@migration("20261116_agent_run_groups", after="20261115_integration_branch_moves")
def m_agent_run_groups(conn: Connection) -> None:
    add_columns_if_missing(conn, "agent_runs", _COLUMNS)
