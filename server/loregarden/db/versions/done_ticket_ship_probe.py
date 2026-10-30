"""The ship probe table: one current row per workspace.

See `models.domain.ship_tables`. Starts empty, and an absent row is read as
UNAVAILABLE ("the probe has not run yet"), never as "every ship is green".
"""

from __future__ import annotations

from loregarden.db.migration_utils import table_exists
from loregarden.db.versions import migration
from sqlalchemy import Connection, text


@migration("20261028_done_ticket_ship_probe", after="20261023_disk_usage")
def m_done_ticket_ship_probe(conn: Connection) -> None:
    if table_exists(conn, "ship_probes"):
        return
    conn.execute(
        text(
            "CREATE TABLE ship_probes ("
            "id VARCHAR NOT NULL PRIMARY KEY, "
            "workspace_id VARCHAR NOT NULL UNIQUE REFERENCES workspaces(id), "
            "probed_at DATETIME NOT NULL, "
            "listing_state VARCHAR NOT NULL DEFAULT 'unavailable', "
            "error VARCHAR NOT NULL DEFAULT '', "
            "violations_json VARCHAR NOT NULL DEFAULT '[]')"
        )
    )
    conn.execute(text("CREATE INDEX ix_ship_probes_workspace_id ON ship_probes (workspace_id)"))
    conn.execute(text("CREATE INDEX ix_ship_probes_listing_state ON ship_probes (listing_state)"))
