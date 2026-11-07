"""How each acceptance criterion of a ticket was answered for (`criterion_tables`).

Nothing is backfilled. The tickets already done were never asked to prove
their criteria, and recording them as met now would invent evidence nobody
produced. `done_tickets_have_settled_criteria` counts only tickets that
finish after this lands.
"""

from __future__ import annotations

from loregarden.db.migration_utils import index_exists, table_exists
from loregarden.db.versions import migration
from sqlalchemy import Connection, text

_INDEXES = {
    "ix_criterion_settlements_ticket_id": (
        "CREATE INDEX ix_criterion_settlements_ticket_id ON criterion_settlements (ticket_id)"
    ),
    "ix_criterion_settlements_delegated_to_ticket_id": (
        "CREATE INDEX ix_criterion_settlements_delegated_to_ticket_id "
        "ON criterion_settlements (delegated_to_ticket_id)"
    ),
}


@migration("20261107_criterion_settlements", after="20261107_ticket_out_of_scope")
def m_criterion_settlements(conn: Connection) -> None:
    if not table_exists(conn, "criterion_settlements"):
        conn.execute(
            text(
                "CREATE TABLE criterion_settlements ("
                "id VARCHAR NOT NULL PRIMARY KEY, "
                "ticket_id VARCHAR NOT NULL REFERENCES tickets(id), "
                "criterion VARCHAR NOT NULL, "
                "disposition VARCHAR NOT NULL, "
                "tests_json VARCHAR NOT NULL DEFAULT '[]', "
                "evidence_artifact_id VARCHAR REFERENCES artifacts(id), "
                "commit_sha VARCHAR NOT NULL DEFAULT '', "
                "delegated_to_ticket_id VARCHAR REFERENCES tickets(id), "
                "by_split BOOLEAN NOT NULL DEFAULT 0, "
                "settled_by VARCHAR NOT NULL DEFAULT '', "
                "settled_at DATETIME NOT NULL, "
                "reason VARCHAR NOT NULL DEFAULT '', "
                "CONSTRAINT uq_criterion_settlement UNIQUE (ticket_id, criterion)"
                ")"
            )
        )
    for name, ddl in _INDEXES.items():
        if not index_exists(conn, name):
            conn.execute(text(ddl))
