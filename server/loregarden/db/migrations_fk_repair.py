"""Repair references that point at rows which do not exist.

Foreign keys were declared but never enforced, so every delete path that missed
a child table, and every write that put the wrong kind of id in a column, left
the reference behind. Enforcement (``db.session._enforce_foreign_keys``) does
not reject rows that are already stored — but it does re-check a row's
references on any UPDATE touching it, so an orphan that sat harmlessly for
months turns into an IntegrityError the next time something edits it. The
artifact upsert path edits rows exactly this way.

The repair is driven by ``PRAGMA foreign_key_check`` rather than a fixed list of
tables, so it fixes whatever a given install actually has rather than what this
checkout's schema happens to declare.
"""

from __future__ import annotations

import logging

from sqlalchemy import text
from sqlalchemy.engine import Connection

logger = logging.getLogger(__name__)


def _violations(conn: Connection) -> list[tuple[str, int, int]]:
    """(table, rowid, fk index) per row whose reference has no parent."""
    return [
        (row[0], row[1], row[3])
        for row in conn.execute(text("PRAGMA foreign_key_check")).fetchall()
        # A WITHOUT ROWID table reports a null rowid; none exist here, and one
        # could not be repaired by rowid anyway.
        if row[1] is not None
    ]


def _foreign_keys(conn: Connection, table: str) -> list[tuple[str, str]]:
    """(column, parent table) per declared foreign key, indexed by fk id."""
    return [
        (row[3], row[2])
        for row in conn.execute(text(f'PRAGMA foreign_key_list("{table}")')).fetchall()
    ]


def _is_nullable(conn: Connection, table: str, column: str) -> bool:
    for row in conn.execute(text(f'PRAGMA table_info("{table}")')).fetchall():
        if row[1] == column:
            return not row[3]
    return False


def m_repair_dangling_references(conn: Connection) -> None:
    """Null every danging reference; delete the rows that cannot hold a null.

    Nulling is preferred: a gate event whose `run_id` named an orchestration run
    is still a true record of the gate, and only its pointer was wrong. A row
    whose reference is NOT NULL has no meaning without its parent — an artifact
    belongs to a ticket, and every read path reaches it through one — so once
    the parent is gone the row is unreachable and is dropped.
    """
    nulled: dict[str, int] = {}
    deleted: dict[str, int] = {}

    for table, rowid, fk_index in _violations(conn):
        keys = _foreign_keys(conn, table)
        if fk_index >= len(keys):
            continue
        column, parent = keys[fk_index]
        label = f"{table}.{column} -> {parent}"
        if _is_nullable(conn, table, column):
            conn.execute(
                text(f'UPDATE "{table}" SET "{column}" = NULL WHERE rowid = :rowid'),
                {"rowid": rowid},
            )
            nulled[label] = nulled.get(label, 0) + 1
        else:
            conn.execute(text(f'DELETE FROM "{table}" WHERE rowid = :rowid'), {"rowid": rowid})
            deleted[label] = deleted.get(label, 0) + 1

    for label, count in sorted(nulled.items()):
        logger.info("fk repair: cleared %d dangling reference(s) in %s", count, label)
    for label, count in sorted(deleted.items()):
        logger.warning(
            "fk repair: deleted %d unreachable row(s) whose %s could not be nulled", count, label
        )
