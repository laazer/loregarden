"""The memory graph file's schema, created and migrated on open.

The graph is a per-workspace SQLite file in a synced vault, not a control-plane
table, so it has no migration ledger: every column added after `memory_nodes`
shipped is applied by `MemoryGraphStore._init_schema` the next time the file is
opened. That logic is cohesive and self-contained — it touches no store state
and returns nothing — so it lives beside `memory_history.ensure_history_schema`
rather than inside the 1500-line store module it is called from.

Each `ensure_*` reads `PRAGMA table_info` itself rather than trusting a caller's
ordering: these run against files written by every version this vault has ever
seen, including ones with the column and ones without.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from typing import Any

logger = logging.getLogger(__name__)

#: Everything a brand-new graph file needs. `IF NOT EXISTS` throughout, because
#: this runs on every open and not only on the first.
BASE_SCHEMA = """
CREATE TABLE IF NOT EXISTS memory_nodes (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    body TEXT NOT NULL DEFAULT '',
    tags_json TEXT NOT NULL DEFAULT '[]',
    ticket_id TEXT NOT NULL DEFAULT '',
    workspace_slug TEXT NOT NULL DEFAULT '',
    node_type TEXT NOT NULL DEFAULT 'memory',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    discredited INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS memory_relations (
    id TEXT PRIMARY KEY,
    source_id TEXT NOT NULL,
    target_id TEXT NOT NULL,
    relation_type TEXT NOT NULL DEFAULT 'related',
    created_at TEXT NOT NULL,
    FOREIGN KEY(source_id) REFERENCES memory_nodes(id),
    FOREIGN KEY(target_id) REFERENCES memory_nodes(id)
);
CREATE INDEX IF NOT EXISTS ix_memory_nodes_ticket ON memory_nodes(ticket_id);
CREATE INDEX IF NOT EXISTS ix_memory_nodes_workspace ON memory_nodes(workspace_slug);
CREATE INDEX IF NOT EXISTS ix_memory_relations_source ON memory_relations(source_id);
"""


def _node_columns(conn: sqlite3.Connection) -> set[str]:
    return {row[1] for row in conn.execute("PRAGMA table_info(memory_nodes)")}


def ensure_node_columns(conn: sqlite3.Connection) -> None:
    """Columns added after `memory_nodes` shipped.

    `aliases_json` defaults to `'[]'` honestly: a node written before aliases
    existed has none, which is a fact rather than an unknown.
    """
    columns = _node_columns(conn)
    if "discredited" not in columns:
        conn.execute("ALTER TABLE memory_nodes ADD COLUMN discredited INTEGER NOT NULL DEFAULT 0")
    if "aliases_json" not in columns:
        conn.execute("ALTER TABLE memory_nodes ADD COLUMN aliases_json TEXT NOT NULL DEFAULT '[]'")


def ensure_origin_columns(conn: sqlite3.Connection) -> None:
    """Provenance columns (766), nullable on purpose.

    A row written before provenance existed has no known origin, and NULL is
    how it says so. A default — `human`, or `agent` inferred from `node_type` —
    would be a guess stored as a fact on every old row.
    """
    columns = _node_columns(conn)
    for column in ("origin_kind", "origin_ref"):
        if column not in columns:
            conn.execute(f"ALTER TABLE memory_nodes ADD COLUMN {column} TEXT NULL")


def ensure_anchor_column(conn: sqlite3.Connection) -> None:
    """Symbol anchors (lg-code-knowledge-368), nullable on purpose.

    NULL means "written ungrounded, or written before anchors existed". `[]`
    is never stored, so "grounded against nothing" cannot become a second
    spelling of the same state.

    The value is opaque JSON to the memory layer, which imports nothing from
    `services.symbol_map`. The graph is per-workspace iCloud SQLite reached by
    raw `sqlite3`; the symbol map is control-plane SQLite behind an ORM
    session. There is no join between them, and the five keys an anchor record
    carries are a contract this side never interprets.
    """
    if "anchors_json" not in _node_columns(conn):
        conn.execute("ALTER TABLE memory_nodes ADD COLUMN anchors_json TEXT NULL")


def decoded_anchors(raw: Any) -> list[dict[str, Any]]:
    """Anchor records from the column, or [] for anything else.

    The column is TEXT in a synced vault operators edit by hand, so a
    half-written or hand-edited value reaches here. Every reader of this key
    indexes `anchor["qualified_name"]` — the search reply, the frontmatter
    exporter and the verdict lookup all do — so the one thing this may not
    hand back is something shaped almost like an anchor.
    """
    if not raw:
        return []
    try:
        decoded = json.loads(raw)
    except (TypeError, ValueError):
        # Reported as "no anchors", which is what an unreadable column honestly
        # says to a reader of the node; the verdict path decodes the same
        # column itself and reports its corruption as a verdict rather than as
        # an absence.
        logger.warning("memory node anchors_json is not JSON; read as unanchored")
        return []
    if type(decoded) is not list:
        logger.warning("memory node anchors_json is not a list; read as unanchored")
        return []
    return [item for item in decoded if type(item) is dict and "qualified_name" in item]
