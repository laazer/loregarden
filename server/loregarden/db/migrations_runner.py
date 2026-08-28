"""Applying the migration list, as distinct from declaring it.

Split out of `migrations.py`, which is a registry: an ordered list of ids and
the functions that perform them. Deciding *which* of those have already run,
warning when the database was written by newer code, and executing the rest is
a separate job with no knowledge of any individual migration.
"""

from __future__ import annotations

import logging

from sqlalchemy import Connection, Engine, text

logger = logging.getLogger(__name__)


def _ensure_migrations_table(conn: Connection) -> None:
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


def _applied_ids(conn: Connection) -> set[str]:
    rows = conn.execute(text("SELECT id FROM schema_migrations")).fetchall()
    return {row[0] for row in rows}


def _warn_if_database_is_ahead(applied_ids: set[str], known_ids: set[str]) -> list[str]:
    """Flag migrations this build has never heard of.

    A migration that rewrites stored values leaves a database only newer code can
    read — check out an older commit, or revert one, and every query over the
    rewritten table fails with a LookupError that says nothing about the real cause.
    The recorded ids say so directly, so name it at startup instead.
    """
    unknown = sorted(applied_ids - known_ids)
    if unknown:
        logger.error(
            "Database has migrations this build does not know about: %s. It was "
            "migrated by newer code, and data those migrations rewrote may not be "
            "readable here. Check out the matching revision rather than running "
            "against it.",
            ", ".join(unknown),
        )
    return unknown


def unknown_migration_ids(engine: Engine, known_ids: set[str]) -> list[str]:
    """Migration ids this database has applied that `known_ids` has never heard of.

    The same set :func:`_warn_if_database_is_ahead` logs, returned so a caller can
    act on it. A build that is behind can still READ a database — the risk is
    writing rows the current code cannot spell, which is how 38 tickets were once
    created carrying `ticket_number = 0` because the writing build predated the
    column.

    Takes `known_ids` rather than importing the registry, for the same cycle
    reason :func:`apply_pending` does.
    """
    if not str(engine.url).startswith("sqlite"):
        return []
    with engine.connect() as conn:
        _ensure_migrations_table(conn)
        return sorted(_applied_ids(conn) - known_ids)


def apply_pending(engine: Engine, migrations: list[tuple[str, object]]) -> list[str]:
    """Apply pending migrations in order. Returns the ids that ran this call.

    Takes the list rather than importing it: the registry imports this module,
    so reaching back for `MIGRATIONS` would close the cycle.

    Runs with foreign-key enforcement off (`db.session` turns it on for every
    connection). SQLite has no ``ALTER COLUMN``, so changing one means
    rebuilding the table — see ``relax_not_null`` — and the rebuild drops the
    original out from under every row referencing it. The pragma is a no-op
    inside a transaction, so it is set before the transaction opens and
    restored before the connection returns to the pool.
    """
    if not str(engine.url).startswith("sqlite"):
        return []
    applied: list[str] = []
    with engine.connect() as conn:
        conn.exec_driver_sql("PRAGMA foreign_keys=OFF")
        # The pragma autobegins a transaction it does not need; end it, or the
        # `conn.begin()` below raises. Pragma state is per connection and
        # survives the rollback.
        conn.rollback()
        try:
            with conn.begin():
                _ensure_migrations_table(conn)
                already = _applied_ids(conn)
                _warn_if_database_is_ahead(already, {mid for mid, _ in migrations})
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
