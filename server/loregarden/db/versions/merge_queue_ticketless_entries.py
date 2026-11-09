"""A merge queue entry may carry a pull request with no ticket behind it.

Interactive sessions open pull requests that belong to no ticket, and until
this they could only reach `main` through GitHub's own strict up-to-date
policy, updating and re-running checks by hand each time `main` moved. The
queue does that for them, so its entries no longer require a ticket.

Measured before this landed (live database, read-only, 2026-11-08): 7 entries,
all from ticket trees, against about 49 pull requests merged to `main` since
the queue shipped on 2026-11-03. Existing rows keep their ticket; the rebuild
only drops the NOT NULL, and is a no-op where it is already gone.

``pushed_sha`` is the last commit the queue pushed onto a PR's head. Such a
PR's head branch is its author's, so a head that is neither the admitted work
nor ``pushed_sha`` is a push the author made while it waited, which the queue
adopts rather than overwrites. Existing rows start empty: every one is a tree's.
"""

from __future__ import annotations

from loregarden.db.migration_utils import add_columns_if_missing, relax_not_null
from loregarden.db.versions import migration
from sqlalchemy import Connection


@migration("20261108_merge_queue_ticketless_entries", after="20261108_stage_required_inputs")
def m_merge_queue_ticketless_entries(conn: Connection) -> None:
    relax_not_null(conn, "merge_queue_entries", "ticket_id")
    add_columns_if_missing(
        conn,
        "merge_queue_entries",
        {
            "pushed_sha": (
                "ALTER TABLE merge_queue_entries ADD COLUMN pushed_sha VARCHAR NOT NULL DEFAULT ''"
            )
        },
    )
