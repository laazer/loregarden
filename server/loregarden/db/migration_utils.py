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


def index_exists(conn: Connection, name: str) -> bool:
    row = conn.execute(
        text("SELECT 1 FROM sqlite_master WHERE type='index' AND name=:name"),
        {"name": name},
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


def column_is_nullable(conn: Connection, table: str, column: str) -> bool:
    for row in conn.execute(text(f"PRAGMA table_info({table})")).fetchall():
        if row[1] == column:
            return not row[3]
    return True


def _quote(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def _column_clause(row, column: str) -> str:
    """One column definition for the rebuilt table, with NOT NULL dropped on ``column``."""
    name, col_type, notnull, default, pk = row[1], row[2], row[3], row[4], row[5]
    parts = [_quote(name), col_type or "VARCHAR"]
    if pk:
        parts.append("PRIMARY KEY")
    if notnull and name != column:
        parts.append("NOT NULL")
    if default is not None:
        parts.append(f"DEFAULT {default}")
    return " ".join(parts)


def _rebuild_table(
    conn: Connection,
    table: str,
    *,
    drop_not_null: str | None = None,
    extra_constraints: list[str] | None = None,
    temp_suffix: str,
) -> None:
    """Rebuild ``table`` from PRAGMA, optionally dropping NOT NULL and appending CHECKs.

    The drop-and-rename is only safe with foreign-key enforcement off — every
    dependant's reference would otherwise break the moment the original table is
    dropped. ``apply_migrations`` turns it off for the duration of the run; this
    helper must not be called on a connection outside that scope.
    """
    info = conn.execute(text(f"PRAGMA table_info({table})")).fetchall()
    names = [row[1] for row in info]
    if drop_not_null:
        definitions = [_column_clause(row, drop_not_null) for row in info]
    else:
        definitions = [_column_clause(row, "") for row in info]

    for fk in conn.execute(text(f"PRAGMA foreign_key_list({table})")).fetchall():
        definitions.append(
            f"FOREIGN KEY({_quote(fk[3])}) REFERENCES {_quote(fk[2])} ({_quote(fk[4])})"
        )

    for constraint in extra_constraints or []:
        definitions.append(constraint)

    indexes = [
        row[0]
        for row in conn.execute(
            text(
                "SELECT sql FROM sqlite_master WHERE type='index' AND tbl_name=:t "
                "AND sql IS NOT NULL"
            ),
            {"t": table},
        ).fetchall()
    ]

    temp = f"{table}__{temp_suffix}"
    column_list = ", ".join(_quote(name) for name in names)
    conn.execute(text(f"CREATE TABLE {_quote(temp)} ({', '.join(definitions)})"))
    conn.execute(
        text(
            f"INSERT INTO {_quote(temp)} ({column_list}) SELECT {column_list} FROM {_quote(table)}"
        )
    )
    conn.execute(text(f"DROP TABLE {_quote(table)}"))
    conn.execute(text(f"ALTER TABLE {_quote(temp)} RENAME TO {_quote(table)}"))
    for statement in indexes:
        conn.execute(text(statement))


def relax_not_null(conn: Connection, table: str, column: str) -> None:
    """Drop the NOT NULL constraint on one column.

    SQLite has no ``ALTER COLUMN``, so the table is rebuilt: the schema is read
    back from ``PRAGMA`` rather than restated here, which keeps the rebuild
    correct no matter which earlier migrations added columns to this table.
    """
    if not table_exists(conn, table) or column_is_nullable(conn, table, column):
        return

    _rebuild_table(
        conn,
        table,
        drop_not_null=column,
        temp_suffix=f"relax_{column}",
    )


def rebuild_drop_not_null_and_add_check(
    conn: Connection,
    table: str,
    column: str,
    *,
    check_name: str,
    check_expr: str,
) -> None:
    """One rebuild that drops NOT NULL on ``column`` and installs a named CHECK.

    Used when both changes must land together — SQLite cannot ADD a CHECK via
    ALTER, and a plain ``relax_not_null`` would leave the table without the
    invariant the CHECK is meant to enforce.
    """
    if not table_exists(conn, table):
        return

    _rebuild_table(
        conn,
        table,
        drop_not_null=column,
        extra_constraints=[f"CONSTRAINT {_quote(check_name)} CHECK ({check_expr})"],
        temp_suffix=f"ck_{column}",
    )


def table_sql(conn: Connection, table: str) -> str | None:
    """CREATE TABLE statement text from sqlite_master, or None if absent."""
    row = conn.execute(
        text("SELECT sql FROM sqlite_master WHERE type='table' AND name=:name"),
        {"name": table},
    ).fetchone()
    return row[0] if row else None
