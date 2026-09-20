"""Where a ticket's work landed (lg-milestone-that-768).

Two columns on tickets. `landed_sha` is the commit on the target branch that
contains the ticket's work — the merge commit `land_ticket` made, or the
branch tip when the target already held it. `landed_branch` is that target.
Empty means the work has not landed, which is not the same as done: a
dependent's readiness (770) reads this, not the state.
"""

from __future__ import annotations

from loregarden.db.migration_utils import add_columns_if_missing
from sqlalchemy.engine import Connection


def m_ticket_landing_columns(conn: Connection) -> None:
    add_columns_if_missing(
        conn,
        "tickets",
        {
            "landed_sha": "ALTER TABLE tickets ADD COLUMN landed_sha VARCHAR NOT NULL DEFAULT ''",
            "landed_branch": (
                "ALTER TABLE tickets ADD COLUMN landed_branch VARCHAR NOT NULL DEFAULT ''"
            ),
        },
    )
