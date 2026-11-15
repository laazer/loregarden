"""Record lg-milestone-that-1316 as delivered, in part, by its rescue.

`20261114_landing_lost_delivery_cleared` cleared 1316's delivery record: its
landing was lost from `integration/lg-milestone-that-491` and six files it
added never reached main (lg-milestone-that-2624). The rescue (#865, merged as
6f3c328e7) restored four of them. A person chose to leave out the other two,
`ToastHostRecovery.test.tsx` and `ToastHostTimer.test.tsx`, because they
pinned behavior that main's clamp fence (lg-external-harness-954) had since
replaced. So the ticket was delivered by that merge, minus those two files,
and the doctor check names that as an exemption (`doctor_landings`).

Guarded by the record it changes: only that ticket, and only while it still
reads cleared or names the delivery commit that did not carry its work.
"""

from __future__ import annotations

from loregarden.db.migration_utils import table_columns, table_exists
from loregarden.db.versions import migration
from sqlalchemy import Connection, text

_TICKET = "lg-milestone-that-1316"
_RESCUE = "6f3c328e7492fad918d38e0ea3da7de270f333ab"
#: The record before the clear, and after it: the only two states this replaces.
_REPLACES = ("", "ed8f3851c763d10bbf97659f5c5722240ad0cd6e")


@migration("20261114_landing_delivered_in_part", after="20261114_landing_lost_delivery_cleared")
def m_landing_delivered_in_part(conn: Connection) -> None:
    if not table_exists(conn, "tickets"):
        return
    if not {"external_id", "on_base_sha"} <= table_columns(conn, "tickets"):
        return
    conn.execute(
        text(
            "UPDATE tickets SET on_base_sha = :rescue "
            "WHERE external_id = :ticket AND on_base_sha IN (:cleared, :wrong)"
        ),
        {"rescue": _RESCUE, "ticket": _TICKET, "cleared": _REPLACES[0], "wrong": _REPLACES[1]},
    )
