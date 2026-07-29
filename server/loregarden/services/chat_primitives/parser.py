"""Parse assistant reply text into ordered chat content parts.

Agents emit fenced `` ```loregarden `` blocks containing JSON with a
``primitive`` discriminator. Everything else becomes a ``text`` part.
Malformed or unknown JSON degrades to text preserving the raw fence —
content is never silently dropped.
"""

from __future__ import annotations

import json
import re
from typing import Any

from loregarden.models.domain.chat_primitives import (
    KNOWN_PRIMITIVES,
    ChatPart,
    TextPart,
)
from pydantic import TypeAdapter, ValidationError

_FENCE_RE = re.compile(
    r"```loregarden\s*\n(.*?)\n```",
    re.DOTALL | re.IGNORECASE,
)

_PART_ADAPTER: TypeAdapter[ChatPart] = TypeAdapter(ChatPart)


def _as_text(content: str) -> TextPart:
    return TextPart(content=content)


def _try_parse_primitive(raw: str) -> ChatPart | None:
    try:
        payload: Any = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    kind = payload.get("primitive")
    if not isinstance(kind, str) or kind not in KNOWN_PRIMITIVES:
        return None
    if kind == "text":
        # Nested text primitives are valid but rare; accept them.
        pass
    try:
        return _PART_ADAPTER.validate_python(payload)
    except ValidationError:
        return None


def parse_primitive_parts(text: str) -> list[ChatPart]:
    """Split *text* into an ordered list of typed chat parts."""
    source = text or ""
    if not source.strip():
        return [_as_text(source)] if source else []

    parts: list[ChatPart] = []
    cursor = 0
    for match in _FENCE_RE.finditer(source):
        before = source[cursor : match.start()]
        if before.strip():
            parts.append(_as_text(before))
        elif before and not parts:
            # Preserve leading whitespace-only only when it is the whole prefix
            # and there is nothing else — normally we skip empty text slices.
            pass

        body = match.group(1).strip()
        parsed = _try_parse_primitive(body)
        if parsed is not None:
            parts.append(parsed)
        else:
            parts.append(_as_text(match.group(0)))
        cursor = match.end()

    trailing = source[cursor:]
    if trailing.strip() or (not parts and trailing):
        parts.append(_as_text(trailing))

    if not parts:
        parts.append(_as_text(source))
    return parts


def parts_to_jsonable(parts: list[ChatPart]) -> list[dict[str, Any]]:
    return [part.model_dump(mode="json") for part in parts]
