"""Discovery runs: an agent searching for references on a topic.

`survey_discoveries` holds each run (topic, focus, how many, the workspaces and
initiatives its finds are for, its carrier ticket); `survey_sources.discovery_id`
names the run that found a source. See `reference_survey.discovery`.
"""

from __future__ import annotations

from loregarden.db.migration_utils import add_columns_if_missing, table_exists
from loregarden.db.versions import migration
from sqlalchemy import Connection, text


@migration("20261023_survey_discoveries", after="20261023_survey_source_description")
def m_survey_discoveries(conn: Connection) -> None:
    if not table_exists(conn, "survey_discoveries"):
        conn.execute(
            text(
                "CREATE TABLE survey_discoveries ( id VARCHAR NOT NULL, workspace_id VARCHAR NOT "
                "NULL, topic VARCHAR(20) NOT NULL, focus VARCHAR NOT NULL, wanted INTEGER NOT NULL, "
                "workspaces_json VARCHAR NOT NULL, initiatives_json VARCHAR NOT NULL, "
                "carrier_ticket_id VARCHAR, created_at DATETIME NOT NULL, PRIMARY KEY (id), "
                "FOREIGN KEY(workspace_id) REFERENCES workspaces (id), "
                "FOREIGN KEY(carrier_ticket_id) REFERENCES tickets (id) )"
            )
        )
        conn.execute(
            text(
                "CREATE INDEX ix_survey_discoveries_workspace_id ON survey_discoveries (workspace_id)"
            )
        )
    add_columns_if_missing(
        conn,
        "survey_sources",
        {
            "discovery_id": (
                "ALTER TABLE survey_sources ADD COLUMN discovery_id VARCHAR "
                "REFERENCES survey_discoveries (id)"
            )
        },
    )
