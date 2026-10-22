"""Where a reference repository stands upstream, as GitHub last reported it.

See `services.reference_survey.upstream`. Added after the survey record shipped
(`20261021_reference_survey_record`), so as columns rather than in that table's
DDL. Not backfilled: a source no check has reached reads "not checked", which is
true, and the background refresh fills them within the day.
"""

from __future__ import annotations

from loregarden.db.migration_utils import add_columns_if_missing
from loregarden.db.versions import migration
from sqlalchemy import Connection

_TABLE = "survey_sources"


@migration("20261022_survey_source_upstream", after="20261021_reference_survey_record")
def m_survey_source_upstream(conn: Connection) -> None:
    add_columns_if_missing(
        conn,
        _TABLE,
        {
            "upstream_branch": f"ALTER TABLE {_TABLE} ADD COLUMN upstream_branch VARCHAR NOT NULL DEFAULT ''",
            "upstream_head": f"ALTER TABLE {_TABLE} ADD COLUMN upstream_head VARCHAR NOT NULL DEFAULT ''",
            "upstream_committed_at": f"ALTER TABLE {_TABLE} ADD COLUMN upstream_committed_at DATETIME",
            "commits_since_review": f"ALTER TABLE {_TABLE} ADD COLUMN commits_since_review INTEGER",
            "upstream_checked_at": f"ALTER TABLE {_TABLE} ADD COLUMN upstream_checked_at DATETIME",
            "upstream_check_error": (
                f"ALTER TABLE {_TABLE} ADD COLUMN upstream_check_error VARCHAR NOT NULL DEFAULT ''"
            ),
        },
    )
