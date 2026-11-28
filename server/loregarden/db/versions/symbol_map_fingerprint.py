"""One column and one index on `symbol_map_symbols` (lg-code-knowledge-368).

Append-only beside `symbol_map.py` rather than an edit to it: that migration
is applied in every live database already, and renaming or rewriting an
applied id runs its body again under a new name.

**No backfill.** The table is a derived index rebuilt wholesale by
`services.symbol_map.store`, so a backfill would have to invent a fingerprint
for a row whose source it never read. Every existing row keeps `''`, which
reads as UNVERIFIABLE — "this anchor's extractor tier produces no
fingerprint" — until the next build re-stamps it.

The ALTER and the CREATE INDEX guard themselves separately, so a run that
added the column and died before the index completes on the next pass rather
than failing.
"""

from __future__ import annotations

from loregarden.db.migration_utils import add_columns_if_missing, index_exists, table_exists
from loregarden.db.versions import migration
from sqlalchemy import Connection, text

_INDEX = "ix_symbol_map_symbol_fingerprint"
_INDEX_DDL = f"CREATE INDEX {_INDEX} ON symbol_map_symbols (partition_id, fingerprint)"


@migration("20261118_symbol_map_fingerprint", after="20261117_symbol_map")
def m_symbol_map_fingerprint(conn: Connection) -> None:
    add_columns_if_missing(
        conn,
        "symbol_map_symbols",
        {
            "fingerprint": (
                "ALTER TABLE symbol_map_symbols ADD COLUMN fingerprint VARCHAR NOT NULL DEFAULT ''"
            )
        },
    )
    if table_exists(conn, "symbol_map_symbols") and not index_exists(conn, _INDEX):
        conn.execute(text(_INDEX_DDL))
