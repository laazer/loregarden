"""Introspection helpers shared by the migration modules.

Lives apart from `migrations.py` so the template-reshaping migrations can use
them without importing the module that imports *them* — the cycle that would
otherwise force the split to be undone.
"""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.engine import Connection


def table_columns(conn: Connection, table: str) -> set[str]:
    rows = conn.execute(text(f"PRAGMA table_info({table})")).fetchall()
    return {row[1] for row in rows}


def table_exists(conn: Connection, table: str) -> bool:
    row = conn.execute(
        text("SELECT name FROM sqlite_master WHERE type='table' AND name=:name"),
        {"name": table},
    ).fetchone()
    return row is not None


def add_columns_if_missing(conn: Connection, table: str, columns: dict[str, str]) -> None:
    """Add each ``name -> ALTER statement`` whose column is absent from ``table``."""
    if not table_exists(conn, table):
        return
    existing = table_columns(conn, table)
    for name, statement in columns.items():
        if name not in existing:
            conn.execute(text(statement))
