"""Parse assistant reply text into ordered chat content parts.

Agents emit fenced `` ```loregarden `` blocks containing JSON with a
``primitive`` discriminator. Everything else becomes a ``text`` part.
Malformed or unknown JSON degrades to text preserving the raw fence —
content is never silently dropped.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from loregarden.models.domain.chat_primitives import (
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


@dataclass(frozen=True)
class RejectedFence:
    """A ``loregarden`` fence that did not become a card, and why.

    ``fence`` is the whole block as it appears in the reply, so a caller can put
    a corrected one in its place. ``kind`` is the ``primitive`` it claimed, when
    the JSON got far enough to say.
    """

    fence: str
    kind: str | None
    reason: str


@dataclass(frozen=True)
class ParsedReply:
    parts: list[ChatPart]
    rejected: list[RejectedFence]


_CLOSER_FOR = {"{": "}", "[": "]"}


def _step_in_string(char: str, *, escaped: bool) -> tuple[bool, bool]:
    """(still in the string, next char escaped) after *char* inside a JSON string."""
    if escaped:
        return True, False
    if char == "\\":
        return True, True
    return char != '"', False


def _close_truncated(raw: str) -> str | None:
    """*raw* with the closers it never wrote, or None when that is not its defect.

    Models regularly drop the last ``}`` (or ``]}``) of a card: everything the
    card says is present and well formed, only the brackets are short. Exactly
    that is repaired. A fence that ends inside a string, closes a bracket it
    never opened, or is short of nothing returns None, so it still degrades to
    visible text rather than to a card guessed from a fragment.
    """
    stack: list[str] = []
    in_string = escaped = False
    for char in raw:
        if in_string:
            in_string, escaped = _step_in_string(char, escaped=escaped)
        elif char == '"':
            in_string = True
        elif char in _CLOSER_FOR:
            stack.append(_CLOSER_FOR[char])
        elif char in "}]":
            if not stack or stack.pop() != char:
                return None
    if in_string or not stack:
        return None
    return raw + "".join(reversed(stack))


def _load_payload(raw: str) -> Any:
    """*raw* as JSON, repairing a dropped tail; raises the original error otherwise."""
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        repaired = _close_truncated(raw)
        if repaired is None:
            raise
        return json.loads(repaired)


_CLAIMED_KIND_RE = re.compile(r'"primitive"\s*:\s*"([\w-]+)"')


def _claimed_kind(raw: str) -> str | None:  # py-org: allow-string - may name no known kind
    match = _CLAIMED_KIND_RE.search(raw)
    return match.group(1) if match else None


def _validation_summary(error: ValidationError) -> str:
    return "; ".join(
        f"{'.'.join(str(loc) for loc in issue['loc'][1:]) or '(card)'}: {issue['msg']}"
        for issue in error.errors()
    )


def _parse_fence(raw: str) -> tuple[ChatPart | None, str]:
    """The card *raw* describes, or None and the reason it describes none.

    The discriminated union rejects a non-object, a missing ``primitive`` and an
    unknown one by itself, each with its own message.
    """
    try:
        payload = _load_payload(raw)
    except json.JSONDecodeError as exc:
        return None, f"invalid JSON: {exc.msg} at line {exc.lineno} column {exc.colno}"
    try:
        return _PART_ADAPTER.validate_python(payload), ""
    except ValidationError as exc:
        return None, _validation_summary(exc)


def parse_reply(text: str) -> ParsedReply:
    """Split *text* into ordered parts, naming every fence that stayed text."""
    source = text or ""
    if not source.strip():
        return ParsedReply(parts=[_as_text(source)] if source else [], rejected=[])

    parts: list[ChatPart] = []
    rejected: list[RejectedFence] = []
    cursor = 0
    for match in _FENCE_RE.finditer(source):
        before = source[cursor : match.start()]
        if before.strip():
            parts.append(_as_text(before))

        body = match.group(1).strip()
        parsed, reason = _parse_fence(body)
        if parsed is None:
            parts.append(_as_text(match.group(0)))
            rejected.append(
                RejectedFence(fence=match.group(0), kind=_claimed_kind(body), reason=reason)
            )
        else:
            parts.append(parsed)
        cursor = match.end()

    trailing = source[cursor:]
    if trailing.strip() or (not parts and trailing):
        parts.append(_as_text(trailing))

    if not parts:
        parts.append(_as_text(source))
    return ParsedReply(parts=parts, rejected=rejected)


def parse_primitive_parts(text: str) -> list[ChatPart]:
    """Split *text* into an ordered list of typed chat parts."""
    return parse_reply(text).parts


def holds_fence(text: str) -> bool:
    """Whether *text* carries a ``loregarden`` fence.

    A stored text part only ever does when that fence failed to parse at write
    time — every fence that parsed became a part of its own.
    """
    return _FENCE_RE.search(text) is not None


#: Parts that are prose rather than a card, so they cannot name a card-only reply.
_PROSE_PRIMITIVES = frozenset({"text", "thinking"})


def reply_preview(text: str) -> str:
    """*text* as one line of prose, for a glance at a reply in a list.

    Card fences are dropped whether or not they parsed: the raw JSON reads as
    noise in a preview, and the card itself is one click away. A reply that is
    only cards is named by its first card instead of left blank.
    """
    prose = " ".join(_FENCE_RE.sub(" ", text or "").split())
    if prose:
        return prose
    for part in parse_primitive_parts(text):
        dumped = part.model_dump()
        if dumped["primitive"] in _PROSE_PRIMITIVES:
            continue
        kind = str(dumped["primitive"]).replace("_", " ")
        return dumped.get("title") or f"{kind.capitalize()} card"
    return ""


def parts_to_jsonable(parts: list[ChatPart]) -> list[dict[str, Any]]:
    return [part.model_dump(mode="json") for part in parts]
