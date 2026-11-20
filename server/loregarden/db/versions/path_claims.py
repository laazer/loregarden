"""Files an agent declared it is about to change (`path_claim_tables`).

Nothing is backfilled: a declaration is made by an agent mid-run, and no run
before this one could make one.
"""

from __future__ import annotations

from loregarden.db.migration_utils import index_exists, table_exists
from loregarden.db.versions import migration
from sqlalchemy import Connection, text


@migration("20261119_path_claims", after="20261117_symbol_map")
def m_path_claims(conn: Connection) -> None:
    if not table_exists(conn, "path_claims"):
        conn.execute(
            text(
                "CREATE TABLE path_claims ("
                "id VARCHAR NOT NULL PRIMARY KEY, "
                "ticket_id VARCHAR NOT NULL REFERENCES tickets(id), "
                "run_id VARCHAR REFERENCES agent_runs(id), "
                "path VARCHAR NOT NULL, "
                "reason VARCHAR NOT NULL DEFAULT '', "
                "created_at DATETIME NOT NULL"
                ")"
            )
        )
    if not index_exists(conn, "ix_path_claims_ticket_id"):
        conn.execute(text("CREATE INDEX ix_path_claims_ticket_id ON path_claims (ticket_id)"))
