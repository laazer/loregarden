"""Clear the delivery record of a landing its integration branch lost.

`base_delivery` took a held integration tip to hold every ticket that ever
landed on the branch. `integration/lg-milestone-that-491` had lost
lg-milestone-that-1316's landing before it was published, and the publish
recorded 1316 as on main anyway, though six of its files never reached it
(lg-milestone-that-2624). Measured read-only on 2026-11-14 across every landed
ticket, it is the only one whose added files never appear in main's history.

With the record cleared, `landings_survive_their_branch` reports it for a
person. Guarded by the record it repairs: only that ticket, only while it
still names the delivery commit that did not carry its work.
"""

from __future__ import annotations

from loregarden.db.migration_utils import table_columns, table_exists
from loregarden.db.versions import migration
from sqlalchemy import Connection, text

_LOST = (("lg-milestone-that-1316", "ed8f3851c763d10bbf97659f5c5722240ad0cd6e"),)


@migration("20261114_landing_lost_delivery_cleared", after="20261112_living_map_refile_chat_order")
def m_landing_lost_delivery_cleared(conn: Connection) -> None:
    if not table_exists(conn, "tickets"):
        return
    # An older schema has no delivery record yet, so nothing to clear.
    if not {"external_id", "on_base_sha"} <= table_columns(conn, "tickets"):
        return
    for external_id, delivered_by in _LOST:
        conn.execute(
            text(
                "UPDATE tickets SET on_base_sha = '' "
                "WHERE external_id = :external_id AND on_base_sha = :delivered_by"
            ),
            {"external_id": external_id, "delivered_by": delivered_by},
        )
