"""Which workspaces one survey pass reads for (`queue.pass_workspaces`).

A pass read for every workspace its source served, so reviewing a loregarden
source again for another project alone could not leave loregarden out. "[]" is
every one, which is what each existing pass did.
"""

from __future__ import annotations

from loregarden.db.migration_utils import add_columns_if_missing
from loregarden.db.versions import migration
from sqlalchemy import Connection


@migration("20261104_survey_pass_workspaces", after="20261103_ticket_on_base_sha")
def m_survey_pass_workspaces(conn: Connection) -> None:
    add_columns_if_missing(
        conn,
        "survey_passes",
        {
            "workspaces_json": (
                "ALTER TABLE survey_passes ADD COLUMN workspaces_json VARCHAR NOT NULL DEFAULT '[]'"
            )
        },
    )
