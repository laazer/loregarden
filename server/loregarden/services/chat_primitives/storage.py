"""Persist and rehydrate the parsed parts of an assistant reply.

Every chat surface parses replies the same way and then has to store the result
next to the message, so the round-trip lives here rather than four times over.
Storing the *resolved* parts is deliberate: a ref resolved at write time keeps
its title after the ticket it points at is renamed or deleted, and rehydrating a
thread must never depend on re-running a resolver over a stale reply.
"""

from __future__ import annotations

import json
from typing import Any

from loregarden.services.chat_primitives.parser import parse_primitive_parts, parts_to_jsonable
from loregarden.services.chat_primitives.resolver import resolve_parts
from sqlmodel import Session

EMPTY_PARTS_JSON = "[]"


def parts_json_for_reply(session: Session, reply: str, *, workspace_id: str) -> str:
    """Parse, resolve, and serialize *reply* for storage on a chat message row."""
    parts = resolve_parts(session, parse_primitive_parts(reply or ""), workspace_id=workspace_id)
    return json.dumps(parts_to_jsonable(parts))


def load_parts_json(parts_json: str | None) -> list[dict[str, Any]]:
    """Stored parts as plain JSON, or an empty list for legacy/plain-text rows.

    Rows written before parts were persisted hold ``[]``, and a thread must still
    render if one holds something unparseable — the message content is always the
    source of truth, parts are the richer view of it.
    """
    if not parts_json:
        return []
    try:
        loaded = json.loads(parts_json)
    except json.JSONDecodeError:
        return []
    if not isinstance(loaded, list):
        return []
    return [item for item in loaded if isinstance(item, dict)]
