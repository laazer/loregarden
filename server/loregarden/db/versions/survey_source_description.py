"""What each reference is, so the library can say without a trip to the site.

Columns on `survey_sources`, filled by the background refresh: GitHub's
description, language, stars and topics from the upstream check it already
makes, and a page's title and opening for sites (`reference_survey.describe`).
Not backfilled here: `described_at` NULL is what the refresh picks up first.
"""

from __future__ import annotations

from loregarden.db.migration_utils import add_columns_if_missing
from loregarden.db.versions import migration
from sqlalchemy import Connection

_TABLE = "survey_sources"


@migration("20261023_survey_source_description", after="20261022_ui_roles_modal_and_hierarchy")
def m_survey_source_description(conn: Connection) -> None:
    add_columns_if_missing(
        conn,
        _TABLE,
        {
            "description": f"ALTER TABLE {_TABLE} ADD COLUMN description VARCHAR",
            "language": f"ALTER TABLE {_TABLE} ADD COLUMN language VARCHAR NOT NULL DEFAULT ''",
            "stars": f"ALTER TABLE {_TABLE} ADD COLUMN stars INTEGER",
            "topics": f"ALTER TABLE {_TABLE} ADD COLUMN topics VARCHAR NOT NULL DEFAULT ''",
            "described_at": f"ALTER TABLE {_TABLE} ADD COLUMN described_at DATETIME",
            "describe_error": (
                f"ALTER TABLE {_TABLE} ADD COLUMN describe_error VARCHAR NOT NULL DEFAULT ''"
            ),
        },
    )
