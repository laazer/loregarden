"""`refresh_started_at` on the symbol map's partitions (lg-code-knowledge-284).

One nullable column, and the only schema change the incremental refresh needs.
`indexed_sha` is already a column on `symbol_map_files` — checked in the tree,
which is what closed the open question the ui-design artifact raised.

The column is what makes `Freshness.BUILDING` reachable rather than a word in
an enum: a refresh stamps it before it writes its first row and clears it when
it commits, so nothing can serve a half-written index as complete.

Guards its own change, so running it twice is a no-op.
"""

from __future__ import annotations

from loregarden.db.migration_utils import add_columns_if_missing
from loregarden.db.versions import migration
from sqlalchemy import Connection


@migration("20261118_symbol_map_refresh", after="20261117_symbol_map")
def m_symbol_map_refresh(conn: Connection) -> None:
    add_columns_if_missing(
        conn,
        "symbol_map_partitions",
        {
            "refresh_started_at": (
                "ALTER TABLE symbol_map_partitions ADD COLUMN refresh_started_at DATETIME"
            )
        },
    )
