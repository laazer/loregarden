"""Raw ``tickets`` INSERT for suites that assert on the real SQLite schema.

The initiative workspace-binding suites check a table-level CHECK constraint, so
they have to insert through SQL rather than the ORM. Spelling the NOT NULL
column list by hand broke the moment ``tickets`` gained another one — the last
was ``landed_sha`` — so the filler columns are read from the schema instead.
"""

from __future__ import annotations

from sqlalchemy import text

#: Columns the callers care about; everything else NOT NULL is filled blank.
_EXPLICIT = {
    "id": ":id",
    "external_id": ":ext",
    "workspace_id": ":ws",
    "title": ":title",
    "work_item_type": ":wit",
    "state": "'backlog'",
    "priority": "3",
    "workflow_stage_status": "'pending'",
    "next_status": "'Proceed'",
}


def _blank_for(sql_type: str) -> str:
    affinity = sql_type.upper()
    if "INT" in affinity or "BOOL" in affinity:
        return "0"
    if "REAL" in affinity or "FLOA" in affinity or "DOUB" in affinity:
        return "0.0"
    if "DATE" in affinity or "TIME" in affinity:
        return "datetime('now')"
    return "''"


def raw_ticket_insert(
    conn,
    *,
    ticket_id: str,
    external_id: str,
    workspace_id: str | None,
    title: str,
    work_item_type: str,
) -> None:
    """INSERT one ticket row, filling every NOT NULL column the schema declares."""
    columns: list[str] = []
    values: list[str] = []
    for _cid, name, sql_type, notnull, default, _pk in conn.execute(
        text("PRAGMA table_info(tickets)")
    ):
        if name in _EXPLICIT:
            columns.append(name)
            values.append(_EXPLICIT[name])
        elif notnull and default is None:
            columns.append(name)
            values.append(_blank_for(sql_type))

    missing = set(_EXPLICIT) - set(columns)
    if missing:
        raise AssertionError(f"tickets is missing expected columns: {sorted(missing)}")

    conn.execute(
        text(f"INSERT INTO tickets ({', '.join(columns)}) VALUES ({', '.join(values)})"),
        {
            "id": ticket_id,
            "ext": external_id,
            "ws": workspace_id,
            "title": title,
            "wit": work_item_type,
        },
    )
