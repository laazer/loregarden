"""A blocked ticket can be deferred so its dependents proceed (`ticket_deferral`).

Nothing is backfilled: no ticket was deferred before this column existed.
"""

from __future__ import annotations

from loregarden.db.migration_utils import add_columns_if_missing
from loregarden.db.versions import migration
from sqlalchemy import Connection

_COLUMNS = {
    "deferred_at": "ALTER TABLE tickets ADD COLUMN deferred_at DATETIME",
    "deferred_reason": "ALTER TABLE tickets ADD COLUMN deferred_reason TEXT NOT NULL DEFAULT ''",
}


@migration("20261108_ticket_deferral", after="20261108_gatekeeper_rejects_on_criteria")
def m_ticket_deferral(conn: Connection) -> None:
    add_columns_if_missing(conn, "tickets", _COLUMNS)
