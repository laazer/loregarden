"""What a ticket's spec ruled out of its scope (lg-milestone-that-1216).

One JSON list column beside the acceptance criteria, in the same shape. The
spec writes it; reviewers and the scope round read it, so a finding about
something already ruled out is not filed as new scope unless the stated reason
is wrong. Starts empty on every ticket.
"""

from __future__ import annotations

from loregarden.db.migration_utils import add_columns_if_missing
from loregarden.db.versions import migration
from sqlalchemy import Connection


@migration("20261107_ticket_out_of_scope", after="20261107_ticket_discoveries")
def m_ticket_out_of_scope(conn: Connection) -> None:
    add_columns_if_missing(
        conn,
        "tickets",
        {
            "out_of_scope_json": (
                "ALTER TABLE tickets ADD COLUMN out_of_scope_json VARCHAR NOT NULL DEFAULT '[]'"
            )
        },
    )
