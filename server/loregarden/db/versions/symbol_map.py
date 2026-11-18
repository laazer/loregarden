"""The per-workspace symbol map's three tables (lg-code-knowledge-281).

See `models.domain.symbol_map_tables`. All three start empty — the map is a
derived index, rebuilt wholesale by `services.symbol_map.store`, so there is
nothing to backfill, and nothing in production builds it when this lands.

Each statement guards itself, so running this twice is a no-op and a partial
build with only some of the indexes is completed rather than failed. The
five index names here are the same five the models declare; a test asserts
the two sets are equal, because a model index the migration never builds
exists in every test database and in no live one — and the query it was
declared for is then a table scan in production only.
"""

from __future__ import annotations

from loregarden.db.migration_utils import index_exists, table_exists
from loregarden.db.versions import migration
from sqlalchemy import Connection, text

_INDEXES = {
    # One partition per (workspace, checkout). The identity is derived from
    # the filesystem, so a database copied into another checkout finds no row.
    "uq_symbol_map_partition": (
        "CREATE UNIQUE INDEX uq_symbol_map_partition "
        "ON symbol_map_partitions (workspace_id, checkout_identity)"
    ),
    # Outline-by-path, and one generation per (partition, path).
    "uq_symbol_map_file_path": (
        "CREATE UNIQUE INDEX uq_symbol_map_file_path ON symbol_map_files (partition_id, path)"
    ),
    # Lookup-by-name across the checkout — the hot path — and the name-only
    # scan `suggest_names` narrows over.
    "ix_symbol_map_symbol_name": (
        "CREATE INDEX ix_symbol_map_symbol_name ON symbol_map_symbols (partition_id, name)"
    ),
    "ix_symbol_map_symbol_qname": (
        "CREATE INDEX ix_symbol_map_symbol_qname "
        "ON symbol_map_symbols (partition_id, qualified_name)"
    ),
    # Outline-by-path's second hop, once the file row is known.
    "ix_symbol_map_symbol_file": (
        "CREATE INDEX ix_symbol_map_symbol_file ON symbol_map_symbols (file_id)"
    ),
}


@migration("20261117_symbol_map", after="20261116_code_signals")
def m_symbol_map(conn: Connection) -> None:
    if not table_exists(conn, "symbol_map_partitions"):
        conn.execute(
            text(
                "CREATE TABLE symbol_map_partitions ("
                "id VARCHAR NOT NULL PRIMARY KEY, "
                "workspace_id VARCHAR NOT NULL REFERENCES workspaces(id), "
                "checkout_identity VARCHAR NOT NULL, "
                "checkout_root VARCHAR NOT NULL, "
                "head_sha VARCHAR NOT NULL DEFAULT '', "
                "built_at DATETIME NOT NULL)"
            )
        )
    if not table_exists(conn, "symbol_map_files"):
        conn.execute(
            text(
                "CREATE TABLE symbol_map_files ("
                "id VARCHAR NOT NULL PRIMARY KEY, "
                "partition_id VARCHAR NOT NULL REFERENCES symbol_map_partitions(id), "
                "path VARCHAR NOT NULL, "
                "language VARCHAR NOT NULL, "
                "extractor_tier VARCHAR NOT NULL, "
                "parse_status VARCHAR NOT NULL, "
                "parse_error VARCHAR NOT NULL DEFAULT '', "
                "indexed_sha VARCHAR NOT NULL DEFAULT '', "
                "index_built_at DATETIME NOT NULL)"
            )
        )
    if not table_exists(conn, "symbol_map_symbols"):
        conn.execute(
            text(
                "CREATE TABLE symbol_map_symbols ("
                "id VARCHAR NOT NULL PRIMARY KEY, "
                "file_id VARCHAR NOT NULL REFERENCES symbol_map_files(id), "
                "partition_id VARCHAR NOT NULL REFERENCES symbol_map_partitions(id), "
                "name VARCHAR NOT NULL, "
                "qualified_name VARCHAR NOT NULL, "
                "kind VARCHAR NOT NULL, "
                "line INTEGER NOT NULL, "
                "parent VARCHAR NOT NULL DEFAULT '')"
            )
        )
    for name, ddl in _INDEXES.items():
        if not index_exists(conn, name):
            conn.execute(text(ddl))
