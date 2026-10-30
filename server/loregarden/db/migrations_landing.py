"""Where a ticket's work landed (lg-milestone-that-768).

Two columns on tickets. `landed_sha` is the **first** commit on `landed_branch`
that contains the ticket's work: the merge commit when a merge brought the
branch in, and the branch tip itself when `landed_branch` fast-forwarded to
it. `landed_branch` is that branch. One meaning, whether the landing ran in
the workflow or was made by hand in git, because it is a property of the git
graph and not of which code path observed it (928).

`publish_tree`'s write satisfies that trivially: it sets `landed_branch` to
the ticket's own branch and `landed_sha` to that branch's tip, which is the
first commit on that branch containing the work. The meaning assumes an
ancestry-visible landing: a branch brought in by a squash merge is not an
ancestor of the target at all, and nothing here says where that landed.

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
