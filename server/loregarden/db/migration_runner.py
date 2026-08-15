"""Applies the migration ledger.

Split from ``migrations.py``, which is the ledger itself plus the migration
bodies: the runner is the part that has to reason about connection and pragma
state, and it grows for different reasons than "one more migration was added".
The registry is passed in rather than imported, so this module does not depend
on the module that holds it.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence

from sqlalchemy import text
from sqlalchemy.engine import Connection, Engine

logger = logging.getLogger(__name__)

Migration = Callable[[Connection], None]


def ensure_migrations_table(conn: Connection) -> None:
    conn.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
                id TEXT PRIMARY KEY,
                applied_at TEXT NOT NULL DEFAULT (datetime('now'))
            )
            """
        )
    )


def applied_ids(conn: Connection) -> set[str]:
    rows = conn.execute(text("SELECT id FROM schema_migrations")).fetchall()
    return {row[0] for row in rows}


def warn_if_database_is_ahead(
    already: set[str], migrations: Sequence[tuple[str, Migration]]
) -> list[str]:
    """Flag migrations this build has never heard of.

    A migration that rewrites stored values leaves a database only newer code can
    read — check out an older commit, or revert one, and every query over the
    rewritten table fails with a LookupError that says nothing about the real cause.
    The recorded ids say so directly, so name it at startup instead.
    """
    unknown = sorted(already - {migration_id for migration_id, _ in migrations})
    if unknown:
        logger.error(
            "Database has migrations this build does not know about: %s. It was "
            "migrated by newer code, and data those migrations rewrote may not be "
            "readable here. Check out the matching revision rather than running "
            "against it.",
            ", ".join(unknown),
        )
    return unknown


def run_migrations(engine: Engine, migrations: Sequence[tuple[str, Migration]]) -> list[str]:
    """Apply pending migrations in order. Returns the ids that ran this call.

    Runs with foreign-key enforcement off. SQLite has no ``ALTER COLUMN``, so
    changing one means rebuilding the table (see ``relax_not_null``) — and a
    rebuild drops the original out from under every row referencing it. The
    pragma is a no-op inside a transaction, so it is set on the connection
    before the transaction opens, and restored before the connection returns to
    the pool.
    """
    if not str(engine.url).startswith("sqlite"):
        return []
    applied: list[str] = []
    with engine.connect() as conn:
        conn.exec_driver_sql("PRAGMA foreign_keys=OFF")
        # The pragma autobegins a SQLAlchemy transaction it does not need; end
        # it, or `conn.begin()` below raises. Pragma state is per connection and
        # survives the rollback.
        conn.rollback()
        try:
            with conn.begin():
                ensure_migrations_table(conn)
                already = applied_ids(conn)
                warn_if_database_is_ahead(already, migrations)
                for migration_id, migrate in migrations:
                    if migration_id in already:
                        continue
                    migrate(conn)
                    conn.execute(
                        text("INSERT INTO schema_migrations (id) VALUES (:id)"),
                        {"id": migration_id},
                    )
                    applied.append(migration_id)
        finally:
            conn.exec_driver_sql("PRAGMA foreign_keys=ON")
            conn.rollback()
    return applied
