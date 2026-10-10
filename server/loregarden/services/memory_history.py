"""Copy-on-write history for graph memory nodes (lg-improved-memory-180).

`MemoryGraphStore.upsert_node` is an `INSERT … ON CONFLICT DO UPDATE`, so every
update used to destroy the body it replaced. A bad prompt could erase a learning
and nothing could say what it had said. This module keeps the replaced version.

What a row means. One row per version that *stopped being current*: the title,
body, tags and flag a node carried before an update replaced them, stamped with
when that happened and, where the call site knew it, who did it and why. The
live row in `memory_nodes` is always the current version; nothing here is.

What it deliberately is not:

- **Not on any hot path.** Recall, `search`, `list_nodes` and the vault export
  read `memory_nodes` alone. History is written inside the update's own
  transaction and read only by an operator asking for it.
- **Not in the control-plane DB.** It lives in the same memory SQLite shard as
  the nodes it records (662), bootstrapped by `CREATE TABLE IF NOT EXISTS` the
  way the shard bootstraps everything else, not by `db/migrations.py`.
- **Not a record of creates.** A create replaces nothing, so it writes no row.
  Nor of an upsert that changes nothing: rewriting identical content supersedes
  no information, and a row for it would spend the retention budget on noise.

Unknown stays unknown. `superseded_by` and `change_note` are NULL when the
writer did not say — never `''`, which would read as "someone said nothing".
A NOT NULL DEFAULT on a field nobody measured manufactures a measurement.

Retention. The newest `HISTORY_KEEP_PER_NODE` versions of each node are kept and
older ones are pruned in the same transaction that adds a new one. Version
numbers are never reused: they count up from the node's highest ever recorded
version, so a pruned history reads as "versions 31–50", not as a node that was
only ever edited twenty times.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass

#: How many superseded versions each node keeps. Twenty is well past the
#: handful of rewrites a learning sees in practice, and bounds a node that some
#: agent loop rewrites on every run to a fixed cost.
HISTORY_KEEP_PER_NODE = 20

_SCHEMA = """
CREATE TABLE IF NOT EXISTS memory_node_versions (
    node_id TEXT NOT NULL,
    version INTEGER NOT NULL,
    title TEXT NOT NULL,
    body TEXT NOT NULL,
    tags_json TEXT NOT NULL,
    discredited INTEGER NOT NULL,
    aliases_json TEXT NULL,
    became_current_at TEXT NOT NULL,
    superseded_at TEXT NOT NULL,
    superseded_by TEXT NULL,
    change_note TEXT NULL,
    PRIMARY KEY (node_id, version)
);
"""


@dataclass(frozen=True, slots=True)
class NodeContent:
    """The fields of a node a version records — everything a writer can change."""

    title: str
    body: str
    tags_json: str
    discredited: bool
    aliases_json: str = "[]"


@dataclass(frozen=True, slots=True)
class ChangeAttribution:
    """Who replaced a version, and why. Either may be unknown (None)."""

    writer: str | None = None
    note: str | None = None


def ensure_history_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(_SCHEMA)
    columns = {row[1] for row in conn.execute("PRAGMA table_info(memory_node_versions)")}
    if "aliases_json" not in columns:
        # NULL on rows written before aliases were versioned: not recorded, not "none".
        conn.execute("ALTER TABLE memory_node_versions ADD COLUMN aliases_json TEXT NULL")


def record_superseded(
    conn: sqlite3.Connection,
    *,
    node_id: str,
    prior: sqlite3.Row,
    incoming: NodeContent,
    superseded_at: str,
    attribution: ChangeAttribution,
    keep: int = HISTORY_KEEP_PER_NODE,
) -> int | None:
    """Preserve `prior` before an update replaces it. Returns the version written.

    `prior` is the live `memory_nodes` row as read inside the caller's
    transaction. Returns None, writing nothing, when `incoming` changes nothing.
    """
    before = NodeContent(
        title=prior["title"],
        body=prior["body"],
        tags_json=prior["tags_json"],
        discredited=bool(prior["discredited"]),
        aliases_json=prior["aliases_json"],
    )
    if before == incoming:
        return None
    (latest,) = conn.execute(
        "SELECT COALESCE(MAX(version), 0) FROM memory_node_versions WHERE node_id = ?",
        (node_id,),
    ).fetchone()
    version = int(latest) + 1
    conn.execute(
        """
        INSERT INTO memory_node_versions (
            node_id, version, title, body, tags_json, discredited, aliases_json,
            became_current_at, superseded_at, superseded_by, change_note
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            node_id,
            version,
            before.title,
            before.body,
            before.tags_json,
            int(before.discredited),
            before.aliases_json,
            prior["updated_at"],
            superseded_at,
            attribution.writer,
            attribution.note,
        ),
    )
    conn.execute(
        "DELETE FROM memory_node_versions WHERE node_id = ? AND version <= ?",
        (node_id, version - keep),
    )
    return version


def list_versions(conn: sqlite3.Connection, node_id: str) -> list[dict[str, object]]:
    """Every retained version of a node, oldest first."""
    rows = conn.execute(
        """
        SELECT node_id, version, title, body, tags_json, discredited, aliases_json,
               became_current_at, superseded_at, superseded_by, change_note
        FROM memory_node_versions
        WHERE node_id = ?
        ORDER BY version ASC
        """,
        (node_id,),
    ).fetchall()
    return [
        {
            "node_id": row["node_id"],
            "version": row["version"],
            "title": row["title"],
            "body": row["body"],
            "tags_json": row["tags_json"],
            "discredited": bool(row["discredited"]),
            "aliases": None if row["aliases_json"] is None else json.loads(row["aliases_json"]),
            "became_current_at": row["became_current_at"],
            "superseded_at": row["superseded_at"],
            "superseded_by": row["superseded_by"],
            "change_note": row["change_note"],
        }
        for row in rows
    ]
