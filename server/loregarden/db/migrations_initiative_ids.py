"""Global initiative_number_pool singleton.

Initiatives share one monotonic counter across every workspace (and none).
Seeded here so create_all/test engines are not the only path that creates the
row — same caution as ``docker_capacity_pool`` (lazy double-insert hazard).
"""

from __future__ import annotations

from loregarden.db.migration_utils import table_exists
from sqlalchemy import text
from sqlalchemy.engine import Connection


def m_initiative_number_pool(conn: Connection) -> None:
    """Create ``initiative_number_pool`` and seed the ``global`` row."""
    if not table_exists(conn, "initiative_number_pool"):
        conn.execute(
            text(
                """
                CREATE TABLE initiative_number_pool (
                    id TEXT PRIMARY KEY,
                    last_initiative_number INTEGER NOT NULL DEFAULT 0
                )
                """
            )
        )

    conn.execute(
        text(
            """
            INSERT INTO initiative_number_pool (id, last_initiative_number)
            SELECT 'global', 0
            WHERE NOT EXISTS (
                SELECT 1 FROM initiative_number_pool WHERE id = 'global'
            )
            """
        )
    )

    seeded = conn.execute(
        text("SELECT COUNT(*) FROM initiative_number_pool WHERE id = 'global'")
    ).scalar()
    if seeded != 1:
        raise RuntimeError(
            "initiative_number_pool singleton was not seeded; initiative creates "
            "would have no counter to advance."
        )
