"""Names, typed edges and supersession for graph memory nodes.

Three rules, borrowed from the second-brain-os maintenance guide because the
graph was quietly breaking each of them:

**A learning has a real title.** Every learning used to be titled
``Learning — <ticket id>``, which names where it came from and nothing about
what it says. That is the key recall scores on, the label the relation digest
shows, and the only thing an operator sees in a list. A title now comes from
the writer or, when an older caller sends none, from the learning's own first
line — the author's words, never invented. The ticket id stays where it
belongs, in `ticket_id`.

**An edge says what it asserts.** `MemoryRelationType` is closed. A label
outside it is refused loudly rather than stored, both endpoints must exist in
the same shard, and re-asserting an existing edge returns it instead of
writing a duplicate.

**Old is not the same as wrong.** A `supersedes` edge marks its target as no
longer current without hiding it; `superseded_by` is how recall finds out, and
`lineage` walks the chain so the question "what changed, and on what
evidence" has an answer — the version history (180) supplies each step's
change notes.
"""

from __future__ import annotations

import sqlite3
from typing import Any
from uuid import uuid4

from loregarden.models.domain.memory_enums import MemoryRelationType, RelationDirection

#: The title every learning carried before titles were required. Kept so
#: maintenance can find the nodes still wearing it (`is_generic_title`).
LEGACY_LEARNING_TITLE_PREFIX = "Learning — "
_TITLE_CHARS = 90


class MemoryRelationError(ValueError):
    """An edge that cannot be written as asked: bad type, missing node, self-loop."""


def learning_title(title: str, content: str) -> tuple[str, bool]:
    """The title to store, and whether it was derived rather than given.

    Derived from the first non-blank line of the content, stripped of markdown
    heading and list markers and cut at a word boundary. Nothing else is
    guessed: content with no usable line gets no title and the write fails.
    """
    given = " ".join(title.split())
    if given:
        return given, False
    for line in content.splitlines():
        words = line.strip().lstrip("#>*-• ").strip()
        if words:
            if len(words) <= _TITLE_CHARS:
                return words.rstrip("."), True
            cut = words[:_TITLE_CHARS].rsplit(" ", 1)[0]
            return f"{cut}…", True
    raise ValueError("A learning needs a title or non-empty content to take one from.")


def is_generic_title(title: str) -> bool:
    return title.startswith(LEGACY_LEARNING_TITLE_PREFIX)


def parse_relation_type(value: str) -> MemoryRelationType:
    """The boundary conversion for a relation label arriving as text."""
    try:
        return MemoryRelationType(value)
    except ValueError as exc:
        allowed = ", ".join(member.value for member in MemoryRelationType)
        raise MemoryRelationError(
            f"Unknown relation type {value!r}. Use one of: {allowed}. Use 'related' for a "
            "plain mention; type an edge only when the relationship is stated."
        ) from exc


def insert_relation(
    conn: sqlite3.Connection,
    *,
    source_id: str,
    target_id: str,
    relation_type: MemoryRelationType,
    created_at: str,
) -> dict[str, Any]:
    """Write one typed edge, or return the identical edge already there."""
    if source_id == target_id:
        raise MemoryRelationError("A node cannot relate to itself.")
    found = {
        row[0]
        for row in conn.execute(
            "SELECT id FROM memory_nodes WHERE id IN (?, ?)", (source_id, target_id)
        )
    }
    missing = [node for node in (source_id, target_id) if node not in found]
    if missing:
        raise MemoryRelationError(f"No memory node with id {', '.join(missing)} in this workspace.")
    existing = conn.execute(
        "SELECT id, created_at FROM memory_relations "
        "WHERE source_id = ? AND target_id = ? AND relation_type = ?",
        (source_id, target_id, relation_type.value),
    ).fetchone()
    if existing:
        relation_id, created_at, created = existing[0], existing[1], False
    else:
        relation_id, created = str(uuid4()), True
        conn.execute(
            "INSERT INTO memory_relations (id, source_id, target_id, relation_type, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (relation_id, source_id, target_id, relation_type.value, created_at),
        )
    return {
        "id": relation_id,
        "source_id": source_id,
        "target_id": target_id,
        "relation_type": relation_type.value,
        "created_at": created_at,
        "created": created,
    }


def superseded_by(conn: sqlite3.Connection, node_ids: list[str]) -> dict[str, list[dict[str, str]]]:
    """For each of `node_ids` that a visible node supersedes, its successors."""
    if not node_ids:
        return {}
    marks = ", ".join("?" for _ in node_ids)
    rows = conn.execute(
        f"""
        SELECT r.target_id, n.id, n.title
        FROM memory_relations r JOIN memory_nodes n ON n.id = r.source_id
        WHERE r.relation_type = ? AND r.target_id IN ({marks})
          AND COALESCE(n.discredited, 0) = 0
        ORDER BY n.updated_at DESC
        """,  # noqa: S608 - placeholders only
        (MemoryRelationType.SUPERSEDES.value, *node_ids),
    ).fetchall()
    successors: dict[str, list[dict[str, str]]] = {}
    for target_id, node_id, title in rows:
        successors.setdefault(target_id, []).append({"id": node_id, "title": title})
    return successors


def relations_of(conn: sqlite3.Connection, node_id: str) -> list[dict[str, Any]]:
    """Every edge touching a node, both directions, with the other end's title
    and flag. For the operator surface, so discredited neighbours are included
    and marked rather than hidden."""
    rows = conn.execute(
        """
        SELECT r.id, r.relation_type, 'out' AS direction, n.id AS node_id, n.title,
               COALESCE(n.discredited, 0) AS discredited
        FROM memory_relations r JOIN memory_nodes n ON n.id = r.target_id
        WHERE r.source_id = ?
        UNION ALL
        SELECT r.id, r.relation_type, 'in' AS direction, n.id AS node_id, n.title,
               COALESCE(n.discredited, 0) AS discredited
        FROM memory_relations r JOIN memory_nodes n ON n.id = r.source_id
        WHERE r.target_id = ?
        """,
        (node_id, node_id),
    ).fetchall()
    return [
        {
            "id": row[0],
            "relation_type": row[1],
            "direction": RelationDirection(row[2]),
            "node_id": row[3],
            "title": row[4],
            "discredited": bool(row[5]),
        }
        for row in rows
    ]


def lineage(conn: sqlite3.Connection, node_id: str, *, max_steps: int = 25) -> list[str]:
    """The supersession chain through `node_id`, oldest first, as node ids.

    Follows `supersedes` edges backwards to the oldest predecessor and forwards
    to the newest successor. Where a node has several, the most recently
    updated is followed; a cycle or `max_steps` stops the walk rather than
    looping.
    """

    def step(current: str, column: str, other: str) -> str | None:
        row = conn.execute(
            f"SELECT r.{other} FROM memory_relations r JOIN memory_nodes n ON n.id = r.{other} "  # noqa: S608 - fixed column names
            f"WHERE r.{column} = ? AND r.relation_type = ? ORDER BY n.updated_at DESC LIMIT 1",
            (current, MemoryRelationType.SUPERSEDES.value),
        ).fetchone()
        return row[0] if row else None

    chain = [node_id]
    seen = {node_id}
    for column, other, prepend in (
        ("target_id", "source_id", False),
        ("source_id", "target_id", True),
    ):
        current = node_id
        for _ in range(max_steps):
            # target_id = current finds a newer node (its source); source_id =
            # current finds an older one (its target).
            nxt = step(current, column, other)
            if nxt is None or nxt in seen:
                break
            seen.add(nxt)
            if prepend:
                chain.insert(0, nxt)
            else:
                chain.append(nxt)
            current = nxt
    return chain
