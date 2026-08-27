"""Migration for the reference-docs fetch-through cache table.

Split out of `migrations.py` the same way `migrations_mcp.py` owns the MCP
cluster: one table, one id, nothing else touches it. Identity is the id string
in the MIGRATIONS list.
"""

from __future__ import annotations

from loregarden.db.migration_utils import table_exists
from sqlalchemy import text
from sqlalchemy.engine import Connection


def m_reference_pages_table(conn: Connection) -> None:
    """Global fetch-through cache for reference MCP tools.

    One row per URL. Fetch, SSRF, and TTL land in later tickets; this
    migration only creates the durable shape they write into.
    """
    if table_exists(conn, "reference_pages"):
        return
    conn.execute(
        text(
            """
            CREATE TABLE reference_pages (
                id TEXT PRIMARY KEY,
                url TEXT NOT NULL,
                title TEXT NOT NULL DEFAULT '',
                content_markdown TEXT NOT NULL DEFAULT '',
                etag TEXT NOT NULL DEFAULT '',
                last_modified TEXT NOT NULL DEFAULT '',
                kind TEXT NOT NULL DEFAULT 'page',
                content_chars INTEGER NOT NULL DEFAULT 0,
                hit_count INTEGER NOT NULL DEFAULT 0,
                fetched_at TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
    )
    conn.execute(text("CREATE UNIQUE INDEX ix_reference_pages_url ON reference_pages (url)"))
