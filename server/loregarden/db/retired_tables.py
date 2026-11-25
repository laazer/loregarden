"""Tables a migration dropped, and an older build can bring back.

`init_db` runs `create_all` before migrations, so any checkout that still has
the model creates the table again the first time its CLI opens the live
database, and the migration that dropped it has already run. The copy is
empty (an older build refuses to write), so the server drops it at boot
until the last such checkout is gone.
"""

from __future__ import annotations

import logging

from loregarden.db.migration_utils import table_exists
from sqlalchemy import Engine, text
from sqlalchemy.exc import OperationalError

logger = logging.getLogger(__name__)

RETIRED_TABLES = ("conflict_reports",)


def drop_retired_tables(engine: Engine) -> list[str]:
    """Drop each retired table that is back and empty; one with rows is kept and reported."""
    dropped: list[str] = []
    for table in RETIRED_TABLES:
        try:
            with engine.begin() as conn:
                if not table_exists(conn, table):
                    continue
                if conn.execute(text(f"SELECT 1 FROM {table} LIMIT 1")).first() is not None:
                    logger.warning("Retired table %s is back and holds rows; it was kept", table)
                    continue
                conn.execute(text(f"DROP TABLE {table}"))
        except OperationalError:
            # Another process holds the write lock. The empty table harms nothing
            # meanwhile, and the next boot tries again.
            logger.warning("Could not drop retired table %s", table, exc_info=True)
            continue
        dropped.append(table)
    return dropped
