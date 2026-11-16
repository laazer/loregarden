"""An artifact's content, as a caller supplied it: a JSON object or nothing."""

from __future__ import annotations

from typing import Any

from pydantic import TypeAdapter, ValidationError

_CONTENT_OBJECT = TypeAdapter(dict[str, Any])


def parse_content_object(raw: str | None) -> dict[str, Any]:
    """`content_json` parsed, refusing anything but a JSON object.

    Every reader of `Artifact.content_json` treats it as an object. A JSON string
    holding an object (an agent that encoded twice) was once accepted and made
    `GET /api/tickets/{id}` fail on every load for that ticket.
    """
    if not raw:
        return {}
    try:
        return _CONTENT_OBJECT.validate_json(raw)
    except ValidationError as exc:
        raise ValueError(
            'content_json must be a JSON object, e.g. \'{"summary": "…"}\'. '
            "If you passed an object as a string, pass it once, not encoded twice."
        ) from exc
