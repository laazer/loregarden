"""Startup check for enum values the ORM cannot read back.

SQLAlchemy resolves an enum column when it *loads* a row, so one unreadable value
does not fail that row — it fails every query that selects the table. A single
hand-written ``blocked`` where the column held ``BLOCKED`` took down each endpoint
listing tickets, and the only symptom was a ``LookupError`` fifteen frames deep in
the result-fetch machinery, repeated on every request.

This turns that into one startup line naming the table, column, value and rows, so
the next occurrence is diagnosable in seconds instead of by reading a stack trace.
It reports; it does not repair. An unexpected value might be a bad write or a
schema that ran ahead of its migration, and those want different answers.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import loregarden.models.domain  # noqa: F401  (registers the tables on SQLModel.metadata)
from loregarden.db.migration_utils import table_exists
from sqlalchemy import Enum as SAEnum
from sqlalchemy import text
from sqlalchemy.engine import Connection, Engine
from sqlmodel import SQLModel

logger = logging.getLogger(__name__)

#: Row identifiers listed per offending value — enough to act on, not a dump.
_MAX_ROWS_REPORTED = 5


@dataclass(frozen=True)
class EnumIntegrityIssue:
    table: str
    column: str
    value: str
    row_ids: list[str]
    expected: list[str]

    def describe(self) -> str:
        rows = ", ".join(self.row_ids) or "unknown"
        return (
            f"{self.table}.{self.column} holds {self.value!r}, which is not a valid "
            f"{self.column} value (expected one of: {', '.join(self.expected)}). "
            f"Affected rows: {rows}. Every query that selects {self.table} will fail "
            f"until this row is corrected."
        )


def _enum_columns() -> list[tuple[str, str, list[str], str]]:
    """(table, column, valid DB-side values, primary key column) for enum columns.

    Reads ``SQLModel.metadata``, which is populated as a side effect of importing the
    model modules — hence the import above. Without it this returns an empty list and
    the check passes by inspecting nothing, which is worse than not having it.
    """
    found = []
    for table in SQLModel.metadata.sorted_tables:
        pk_columns = list(table.primary_key.columns)
        pk = pk_columns[0].name if pk_columns else ""
        for col in table.columns:
            if isinstance(col.type, SAEnum) and col.type.enum_class:
                found.append((table.name, col.name, list(col.type.enums), pk))
    return found


def find_unreadable_enum_values(conn: Connection) -> list[EnumIntegrityIssue]:
    issues: list[EnumIntegrityIssue] = []
    for table, column, valid, pk in _enum_columns():
        if not table_exists(conn, table):
            continue
        # Identifiers come from our own mapped metadata, never from input.
        distinct = conn.execute(
            text(f'SELECT DISTINCT "{column}" FROM "{table}"')  # noqa: S608
        ).fetchall()
        for (value,) in distinct:
            if value is None or value in valid:
                continue
            row_ids: list[str] = []
            if pk:
                rows = conn.execute(
                    text(  # noqa: S608
                        f'SELECT "{pk}" FROM "{table}" WHERE "{column}" = :value LIMIT :limit'
                    ),
                    {"value": value, "limit": _MAX_ROWS_REPORTED},
                ).fetchall()
                row_ids = [str(r[0]) for r in rows]
            issues.append(
                EnumIntegrityIssue(
                    table=table,
                    column=column,
                    value=str(value),
                    row_ids=row_ids,
                    expected=valid,
                )
            )
    return issues


def report_unreadable_enum_values(engine: Engine) -> list[EnumIntegrityIssue]:
    """Log any unreadable enum values. Returns them so callers can assert on them."""
    with engine.connect() as conn:
        issues = find_unreadable_enum_values(conn)
    for issue in issues:
        logger.error("Unreadable enum value in the database: %s", issue.describe())
    return issues
