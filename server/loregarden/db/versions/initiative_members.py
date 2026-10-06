"""Initiative membership: a ticket an initiative tracks without parenting it.

See `models.domain.initiative_tables.InitiativeMember`. Nothing is backfilled:
every existing initiative covers exactly the tickets it parents, as before.
"""

from __future__ import annotations

from loregarden.db.migration_utils import index_exists, table_exists
from loregarden.db.versions import migration
from sqlalchemy import Connection, text


@migration("20261006_initiative_members", after="20261005_queue_lane_count")
def m_initiative_members(conn: Connection) -> None:
    if not table_exists(conn, "initiative_members"):
        conn.execute(
            text(
                "CREATE TABLE initiative_members ("
                "initiative_id VARCHAR NOT NULL REFERENCES tickets(id), "
                "ticket_id VARCHAR NOT NULL REFERENCES tickets(id), "
                "added_at DATETIME NOT NULL, "
                "added_by VARCHAR NOT NULL DEFAULT '', "
                "PRIMARY KEY (initiative_id, ticket_id))"
            )
        )
    if not index_exists(conn, "ix_initiative_members_ticket_id"):
        conn.execute(
            text("CREATE INDEX ix_initiative_members_ticket_id ON initiative_members (ticket_id)")
        )
