"""Persist and rehydrate the parsed parts of an assistant reply.

Every chat surface parses replies the same way and then has to store the result
next to the message, so the round-trip lives here rather than four times over.
Storing the *resolved* parts is deliberate: a ref resolved at write time keeps
its title after the ticket it points at is renamed or deleted, and rehydrating a
thread must never depend on re-running a resolver over a stale reply.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any

from loregarden.models.domain.chat_primitives import TextPart
from loregarden.services.chat_primitives.parser import (
    RejectedFence,
    holds_fence,
    parse_primitive_parts,
    parse_reply,
    parts_to_jsonable,
)
from loregarden.services.chat_primitives.repair import CardCorrector, correct_rejected_cards
from loregarden.services.chat_primitives.resolver import resolve_parts
from pydantic import ValidationError
from sqlmodel import Session

logger = logging.getLogger(__name__)

EMPTY_PARTS_JSON = "[]"


@dataclass(frozen=True)
class SettledReply:
    """What a chat message row stores for one assistant reply.

    ``content`` can differ from the reply the model gave: it carries any card the
    correction turn fixed, so the stored text and its parts never disagree.
    """

    content: str
    parts_json: str


def settle_reply(
    session: Session,
    reply: str,
    *,
    workspace_id: str | None,
    corrector: CardCorrector | None,
) -> SettledReply:
    """Parse, correct, resolve, and serialize *reply* for a chat message row.

    ``corrector`` gets one chance at every card that failed to parse (see
    ``repair``); ``None`` stores the reply as given, for content that is not a
    model's reply (a failure message, a stub). ``workspace_id=None`` resolves
    refs across workspaces (see ``resolver``).
    """
    content = reply or ""
    rejected = parse_reply(content).rejected
    if rejected and corrector is not None:
        content, rejected = _corrected(content, rejected, corrector)
    for fence in rejected:
        logger.warning("A %s card stayed raw text: %s", fence.kind or "chat", fence.reason)
    parts = resolve_parts(session, parse_primitive_parts(content), workspace_id=workspace_id)
    return SettledReply(content=content, parts_json=json.dumps(parts_to_jsonable(parts)))


def _corrected(
    content: str, rejected: list[RejectedFence], corrector: CardCorrector
) -> tuple[str, list[RejectedFence]]:
    try:
        return correct_rejected_cards(content, rejected, corrector)
    except (RuntimeError, ValueError, OSError):
        # What a CLI turn raises: a failed or empty run, a missing agent or repo,
        # a spawn failure or a timeout. The reply is whole without the correction:
        # every rejected card is still in it as raw text the operator can read,
        # and each is logged by the caller.
        logger.warning("Card correction turn failed; keeping the reply as written", exc_info=True)
        return content, rejected


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
        # The thread still renders from message content, but a row that cannot be
        # parsed is corruption, not a legacy plain-text row.
        logger.warning("Unreadable parts_json; falling back to plain message content")
        return []
    if not isinstance(loaded, list):
        return []
    # An unmodeled JSON column: entries that are not objects were always dropped.
    objects = [item for item in loaded if isinstance(item, dict)]  # py-org: allow-isinstance
    return [reparsed for item in objects for reparsed in _reparse_rejected_text(item)]


def _reparse_rejected_text(part: dict[str, Any]) -> list[dict[str, Any]]:
    """*part*, or the cards today's parser makes of a fence an older one rejected.

    Parts are stored resolved and are otherwise served as written. A text part
    holding a fence is the exception: it holds one only because that fence
    failed to parse when the row was written, so every parser fix since then
    applies to it. It is re-parsed but not re-resolved, because readers have no
    session; a recovered card keeps its refs as the model wrote them, and the
    client looks them up live.
    """
    if part.get("primitive") != "text":
        return [part]
    try:
        text = TextPart.model_validate(part)
    except ValidationError:
        # silent-ok: served exactly as stored, which is what every reader did before
        # re-parsing existed; the client renders a malformed text part as text.
        return [part]
    if not holds_fence(text.content):
        return [part]
    return parts_to_jsonable(parse_primitive_parts(text.content))
