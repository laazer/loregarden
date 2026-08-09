"""Ticket tags — the one place a ``list[str]`` becomes stored ``tags_json``.

Mirrors ``acceptance_criteria``: every writer (PATCH, MCP update_ticket) routes
through ``serialize_tags`` so normalization cannot drift between entry points.

Tags are compared case-insensitively but stored as typed, so "Backend" and
"backend" are the same tag and the first spelling wins. Kept free of service
imports so anything below the service layer can reach it.
"""

import json
from collections.abc import Iterable

#: Long enough for a readable label, short enough to stay a pill in the UI.
MAX_TAG_LENGTH = 32
#: A guard against a runaway agent turning a ticket into a tag dump.
MAX_TAGS = 20


def normalize_tags(tags: Iterable[str] | None) -> list[str]:
    """Strip, drop blanks, and drop case-insensitive duplicates, order preserved.

    Raises ``ValueError`` on a tag that is too long or on too many tags — a
    silently truncated label is worse than a rejected write.
    """
    result: list[str] = []
    seen: set[str] = set()
    for raw in tags or []:
        tag = raw.strip()
        if not tag:
            continue
        if len(tag) > MAX_TAG_LENGTH:
            raise ValueError(f"Tag is longer than {MAX_TAG_LENGTH} characters: {tag!r}")
        key = tag.casefold()
        if key in seen:
            continue
        seen.add(key)
        result.append(tag)
    if len(result) > MAX_TAGS:
        raise ValueError(f"A ticket can carry at most {MAX_TAGS} tags (got {len(result)})")
    return result


def load_tags(raw: str | None) -> list[str]:
    """Read a ticket's stored ``tags_json``.

    Tolerates the empty/NULL column on rows written before the field existed, and
    never raises on stored content — the limits above guard writes, and a row that
    predates them must still be readable.
    """
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return []
    if not isinstance(parsed, list):
        return []
    return [item.strip() for item in parsed if isinstance(item, str) and item.strip()]


def serialize_tags(tags: Iterable[str] | None) -> str:
    """Normalize and encode tags for ``Ticket.tags_json``."""
    return json.dumps(normalize_tags(tags))
