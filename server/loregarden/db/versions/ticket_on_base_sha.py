"""Where a ticket's landed work is on the base (`services.base_delivery`).

`landed_sha` says the work landed, usually on a tree's integration branch, and
`done` follows from it. Neither says the base has it: that takes a publish and
a merged PR, and 679's tests sat on an unpublished integration branch for three
weeks with every ticket on it reading done. `on_base_sha` is the base commit
that holds the work, recorded once the base is seen holding it.

Starts empty for every row; the reconcile step fills in the ones the base
already holds on its first pass.
"""

from __future__ import annotations

from loregarden.db.migration_utils import add_columns_if_missing
from loregarden.db.versions import migration
from sqlalchemy import Connection


@migration("20261103_ticket_on_base_sha", after="20261028_done_ticket_ship_probe")
def m_ticket_on_base_sha(conn: Connection) -> None:
    add_columns_if_missing(
        conn,
        "tickets",
        {"on_base_sha": "ALTER TABLE tickets ADD COLUMN on_base_sha VARCHAR NOT NULL DEFAULT ''"},
    )
