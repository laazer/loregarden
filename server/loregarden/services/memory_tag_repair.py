"""Rejoin tags that were stored as fragments of a JSON array.

Until `mcp.tool_args.coerce_tag_list`, the memory tools split every string
`tags`/`aliases` argument on commas, including the JSON arrays their own schema
invited. `'["routing", "rework"]'` was stored as the two tags `'["routing"'`
and `'"rework"]'`. Seen on loregarden's live graph (lg-workflow-integrity-765).

Run on every graph open, like the column migrations beside it: it touches only
rows holding a tag that starts with `[` (`"[` inside the JSON column), so a
repaired graph costs one LIKE scan per column and no writes.
"""

from __future__ import annotations

import json
import logging
import sqlite3

from pydantic import TypeAdapter, ValidationError

logger = logging.getLogger(__name__)

_COLUMNS = ("tags_json", "aliases_json")
#: A rejoined run counts only if it is a JSON array of strings.
_STRING_LIST = TypeAdapter(list[str])


def _parse_run(items: list[str], start: int) -> tuple[list[str], int] | None:
    """The strings of the shortest JSON array beginning at `items[start]`, and
    the index after it — or None when no run from there parses."""
    for end in range(start, len(items)):
        if not items[end].endswith("]"):
            continue
        candidate = ",".join(items[start : end + 1])
        try:
            parsed = _STRING_LIST.validate_json(candidate)
        except ValidationError:  # silent-ok: not this `]`; the caller keeps an unparsed run
            continue
        return [p.strip() for p in parsed if p.strip()], end + 1
    return None


def _rejoin(items: list[str]) -> list[str]:
    """`items` with every `[`…`]` run of fragments parsed back into its strings.

    A run that does not parse as a JSON array of strings is left as it was — a
    guess at what it meant would be worse than the visible malformation.
    """
    result: list[str] = []
    i = 0
    while i < len(items):
        run = _parse_run(items, i) if items[i].startswith("[") else None
        if run is None:
            result.append(items[i])
            i += 1
        else:
            result.extend(run[0])
            i = run[1]
    # The split also stripped each fragment, so a tag the JSON repeated appears twice.
    return list(dict.fromkeys(result))


def repair_split_json_tags(conn: sqlite3.Connection) -> int:
    """Rewrite rows whose tags or aliases hold JSON fragments; return rows changed."""
    changed = 0
    for column in _COLUMNS:
        rows = conn.execute(
            f"SELECT id, {column} FROM memory_nodes WHERE {column} LIKE ?",  # noqa: S608 - column from a fixed tuple
            ('%"[%',),
        ).fetchall()
        for node_id, raw in rows:
            items = json.loads(raw or "[]")
            repaired = _rejoin(items)
            if repaired == items:
                continue
            conn.execute(
                f"UPDATE memory_nodes SET {column} = ? WHERE id = ?",  # noqa: S608 - column from a fixed tuple
                (json.dumps(repaired), node_id),
            )
            changed += 1
    if changed:
        logger.warning(
            "memory graph: rejoined JSON-fragment tags/aliases on %d row(s) "
            "(written by the pre-fix comma split)",
            changed,
        )
    return changed
