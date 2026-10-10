"""The provider and model an initiative's planner conversation runs on.

See `models.domain.initiative_tables.InitiativePlan.planner_runtime_json`. The
default `'{}'` is "no override", which is what every planner turn ran with
before: the workspace's own runtime.
"""

from __future__ import annotations

from loregarden.db.migration_utils import add_columns_if_missing
from loregarden.db.versions import migration
from sqlalchemy import Connection


@migration("20261007_initiative_planner_runtime", after="20261006_initiative_members")
def m_initiative_planner_runtime(conn: Connection) -> None:
    add_columns_if_missing(
        conn,
        "initiative_plans",
        {
            "planner_runtime_json": (
                "ALTER TABLE initiative_plans "
                "ADD COLUMN planner_runtime_json TEXT NOT NULL DEFAULT '{}'"
            ),
        },
    )
