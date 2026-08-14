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


def apply_pending(engine: Engine, migrations: list[tuple[str, object]]) -> list[str]:
    """Apply pending migrations in order. Returns the ids that ran this call.

    Takes the list rather than importing it: the registry imports this module,
    so reaching back for `MIGRATIONS` would close the cycle.
    """
    if not str(engine.url).startswith("sqlite"):
        return []
    applied: list[str] = []
    with engine.begin() as conn:
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
    return applied
