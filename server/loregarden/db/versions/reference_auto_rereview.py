"""Auto re-review of reference repositories (`reference_survey.rereview`).

`reference_rereview_settings` holds the one switch and two numbers; absent rows
read as the defaults (off). `survey_sources.auto_rereview_excluded` is a
person's opt-out; `survey_passes.since_commit` and `changes` are what a
re-review pass reads. Nothing is backfilled: every source starts included, and
nothing is queued until someone turns it on.
"""

from __future__ import annotations

from loregarden.db.migration_utils import add_columns_if_missing, table_exists
from loregarden.db.versions import migration
from sqlalchemy import Connection, text


@migration("20261105_reference_auto_rereview", after="20261104_survey_pass_workspaces")
def m_reference_auto_rereview(conn: Connection) -> None:
    if not table_exists(conn, "reference_rereview_settings"):
        conn.execute(
            text(
                "CREATE TABLE reference_rereview_settings ("
                "id VARCHAR NOT NULL PRIMARY KEY, "
                "enabled BOOLEAN NOT NULL DEFAULT 0, "
                "min_new_commits INTEGER NOT NULL DEFAULT 25, "
                "max_commits_per_review INTEGER NOT NULL DEFAULT 50, "
                "updated_at DATETIME NOT NULL)"
            )
        )
    add_columns_if_missing(
        conn,
        "survey_sources",
        {
            "auto_rereview_excluded": (
                "ALTER TABLE survey_sources ADD COLUMN auto_rereview_excluded "
                "BOOLEAN NOT NULL DEFAULT 0"
            )
        },
    )
    add_columns_if_missing(
        conn,
        "survey_passes",
        {
            "since_commit": "ALTER TABLE survey_passes ADD COLUMN since_commit VARCHAR NOT NULL DEFAULT ''",
            "changes": "ALTER TABLE survey_passes ADD COLUMN changes VARCHAR NOT NULL DEFAULT ''",
        },
    )
