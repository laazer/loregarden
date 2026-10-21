"""Close the open stages every closed ticket was left with.

The backlog half of `db.closed_workflow_guard`: 361 of 466 closed tickets on
the live database had a stage still pending, running, awaiting or blocked when
the guard landed. Repaired here rather than by the invariant's first pass,
because `workflow_instances.updated_at` is bumped by bulk writes and reads alike,
so the invariant would date old rows as new and report them as re-entries. After
this, anything `closed_tickets_have_closed_workflows` finds really did get past
the guard.

Uses the guard's own `close_stage_rows`, so the notes match and a reopen restores
these stages exactly as it restores any other. Raw SQL, so no ORM flush runs and
neither timestamp moves: a repair is not a decision about the ticket.
"""

from __future__ import annotations

from loregarden.db.closed_workflow_guard import CLOSED_STATES, close_stage_rows
from loregarden.db.migration_utils import table_exists
from loregarden.db.versions import migration
from loregarden.models.domain import TicketState
from sqlalchemy import Connection, bindparam, text


@migration("20261020_closed_tickets_close_workflows", after="20261020_fix_coverage_lanes")
def m_closed_tickets_close_workflows(conn: Connection) -> None:
    if not (table_exists(conn, "tickets") and table_exists(conn, "workflow_instances")):
        return
    rows = conn.execute(
        text(
            "SELECT t.id, t.state, t.workflow_stage_key, w.id, w.stages_json "
            "FROM tickets t JOIN workflow_instances w ON w.ticket_id = t.id "
            "WHERE t.state IN :states"
        ).bindparams(bindparam("states", expanding=True)),
        {"states": [state.value for state in CLOSED_STATES]},
    ).all()
    for ticket_id, state, cursor_key, instance_id, stages_json in rows:
        change = close_stage_rows(stages_json, TicketState(state))
        if change.keys:
            conn.execute(
                text("UPDATE workflow_instances SET stages_json = :j WHERE id = :id"),
                {"j": change.stages_json, "id": instance_id},
            )
        # The cursor shows its stage's status, closed or not: every closed
        # cursor on the live database had its row, so the subquery always finds one.
        conn.execute(
            text(
                "UPDATE tickets SET workflow_stage_status = ("
                " SELECT json_extract(j.value, '$.status') FROM json_each(:j) j"
                " WHERE json_extract(j.value, '$.key') = :k)"
                " WHERE id = :id AND EXISTS ("
                " SELECT 1 FROM json_each(:j) j WHERE json_extract(j.value, '$.key') = :k)"
            ),
            {"j": change.stages_json, "k": cursor_key, "id": ticket_id},
        )
