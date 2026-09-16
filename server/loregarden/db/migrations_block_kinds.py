"""`tickets.block_kind` — who can unblock a blocked ticket (749).

Not the retired `0085_ticket_blocked_kind` from an unmerged branch (see
`migrations_ledger`): a new column under a new name, nullable, no backfill —
the reconciler classifies existing blocks by their message on its next pass.
"""

from __future__ import annotations

from loregarden.db.migration_utils import add_columns_if_missing, table_exists
from sqlalchemy.engine import Connection


def m_ticket_block_kind(conn: Connection) -> None:
    if table_exists(conn, "tickets"):
        add_columns_if_missing(
            conn, "tickets", {"block_kind": "ALTER TABLE tickets ADD COLUMN block_kind TEXT"}
        )
