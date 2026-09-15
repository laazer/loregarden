"""Nullable workspace_id for initiatives, with a CHECK invariant.

``workspace_id IS NULL`` iff ``work_item_type = 'initiative'``. Plain
``relax_not_null`` is not enough — SQLite cannot ADD a CHECK via ALTER, so this
rebuilds ``tickets`` once under the FK-off window ``apply_migrations`` already
opens.

Does not UPDATE any existing ``workspace_id`` values. If a pre-existing row
would violate the new CHECK, the migration aborts with a clear error rather than
rewriting data (731-era initiatives may still be workspace-bound).
"""

from __future__ import annotations

from loregarden.db.migration_utils import (
    column_is_nullable,
    rebuild_drop_not_null_and_add_check,
    table_exists,
    table_sql,
)
from sqlalchemy import text
from sqlalchemy.engine import Connection

MIGRATION_ID = "0131_tickets_workspace_binding"

CHECK_NAME = "ck_tickets_workspace_binding"
CHECK_EXPR = "(workspace_id IS NULL) = (work_item_type = 'initiative')"


def _has_binding_check(conn: Connection) -> bool:
    sql = table_sql(conn, "tickets")
    if not sql:
        return False
    normalized = sql.replace(" ", "").lower()
    return "workspace_idisnull" in normalized and "work_item_type='initiative'" in normalized


def _abort_if_rows_violate(conn: Connection) -> None:
    rows = conn.execute(
        text(
            "SELECT id, work_item_type, workspace_id FROM tickets "
            "WHERE (workspace_id IS NULL) != (work_item_type = 'initiative')"
        )
    ).fetchall()
    if not rows:
        return
    detail = ", ".join(
        f"{row[0]} (work_item_type={row[1]!r}, workspace_id={row[2]!r})" for row in rows[:20]
    )
    extra = "" if len(rows) <= 20 else f" … and {len(rows) - 20} more"
    raise ValueError(
        f"Cannot apply {MIGRATION_ID}: {len(rows)} ticket row(s) violate "
        f"workspace binding (workspace_id IS NULL iff work_item_type='initiative'): "
        f"{detail}{extra}"
    )


def m_tickets_workspace_binding(conn: Connection) -> None:
    if not table_exists(conn, "tickets"):
        return
    if column_is_nullable(conn, "tickets", "workspace_id") and _has_binding_check(conn):
        return

    _abort_if_rows_violate(conn)

    rebuild_drop_not_null_and_add_check(
        conn,
        "tickets",
        "workspace_id",
        check_name=CHECK_NAME,
        check_expr=CHECK_EXPR,
    )
