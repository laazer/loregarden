"""Baxter's reasoning, delivered while he is still reasoning.

Every chat surface used to show one static line — "Baxter is looking…" — for
the whole of a turn that can run for minutes across a dozen tool calls. The CLI
was already telling us what it was doing; nothing was listening on the chat
path, because the only sink the permission bridge knew about writes a
ticket-scoped log artifact and a Home chat turn has no ticket.

This module is that listener. It reads the same stream-json the bridge reads,
keeps the reasoning and tool activity, and pushes both two ways:

* to ``chat_turn_thinking``, the durable copy a late or reloaded reader starts
  from;
* to the event hub, so an open panel sees each chunk as it lands rather than at
  the next poll.

When the turn settles, ``finish_chat_turn_thinking`` hands the transcript back
so it can be folded into the message as a collapsed thinking part — the
reasoning stays with the answer it produced instead of vanishing with the run.

The reply text rides alongside, in its own field. It is deliberately never
folded into the message — the settled message *is* the reply — but it has to
stream, because a read-only turn emits an empty thinking block and the reply is
then the only thing that moves. Without it those surfaces would show a live
panel with nothing in it.
"""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone
from typing import Any

from loregarden.db.session import engine
from loregarden.models.domain import ChatTurnThinking
from loregarden.models.domain.chat_primitives import ThinkingPart
from loregarden.services.event_hub import event_hub
from sqlmodel import Session, select

logger = logging.getLogger(__name__)

#: Cap on the retained transcript. Long enough to hold a real investigation,
#: short enough that a runaway loop cannot grow one row without bound. The tail
#: is what is kept: the last thing an agent thought explains where it is now.
MAX_THINKING_CHARS = 24_000

#: Cap on a single tool label, which can carry a whole command line.
MAX_ACTIVITY_CHARS = 160

#: How often an accumulating stream is written down and published. Token deltas
#: arrive far faster than anyone can read; at this rate the panel moves
#: continuously and the row is written a few times a second, not hundreds.
FLUSH_INTERVAL_SECONDS = 0.25


def chat_turn_topic(turn_id: str) -> str:
    """The event-hub topic carrying one turn's reasoning."""
    return f"chat-turn:{turn_id}"


def _tool_label(name: str, tool_input: Any) -> str:
    """A tool call as one readable line: the name, plus what it is aimed at."""
    target = ""
    if isinstance(tool_input, dict):
        for key in ("file_path", "path", "pattern", "command", "url", "ticket_id", "query"):
            value = tool_input.get(key)
            if isinstance(value, str) and value.strip():
                target = value.strip().splitlines()[0]
                break
    label = f"{name} · {target}" if target else name
    return label[:MAX_ACTIVITY_CHARS]


class ChatTurnThinkingSink:
    """A ``RunStreamSink`` that turns one turn's stream-json into live thinking.

    Owns its own database sessions rather than borrowing the caller's: the
    service that started the turn is blocked inside the bridge for the whole
    run, and a session cannot be shared across that boundary.
    """

    def __init__(self, turn_id: str) -> None:
        self.turn_id = turn_id
        self._topic = chat_turn_topic(turn_id)
        self._content = ""
        self._answer = ""
        self._activity = ""
        self._seq = 0
        self._pending = False
        self._last_flush = 0.0
        #: Once token deltas are seen, the completed ``assistant`` snapshot of
        #: the same blocks is ignored — otherwise every thought lands twice.
        self._saw_deltas = False

    # -- ingestion ---------------------------------------------------------

    def append_stream_line(self, raw_line: str) -> None:
        line = raw_line.strip()
        if not line:
            return
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            return
        if not isinstance(payload, dict):
            return
        self._ingest(payload)
        self._maybe_flush()

    def append(self, tag: str, text: str, *, force: bool = False) -> None:
        """Bridge-authored lines: what Loregarden decided, not what the agent did.

        The tool calls themselves come off the stream, where they carry their
        arguments; the bridge's version is name-only and would double every
        step. What it knows and the stream does not is when a call was refused,
        and when a human steered the run — both change what happens next, so
        both belong in the transcript.
        """
        if tag == "STEER":
            self._add_event(f"Steered: {text}")
        elif tag == "TOOL" and text.startswith("Denied"):
            self._add_event(text)
        else:
            return
        self._maybe_flush(force=True)

    def set_live(self, text: str) -> None:
        """Ignored: the activity line here is derived from the stream itself.

        The bridge's version of it is a fixed "Agent running…", which would
        overwrite the tool name the operator actually wants to see.
        """

    def touch(self) -> None:
        self._maybe_flush()

    def close(self) -> None:
        """Final write, so the last chunk is not lost to the flush interval."""
        self._maybe_flush(force=True)

    # -- stream-json -------------------------------------------------------

    def _ingest(self, payload: dict[str, Any]) -> None:
        kind = payload.get("type")
        # `--include-partial-messages` wraps raw Anthropic events in an envelope.
        if kind == "stream_event":
            inner = payload.get("event")
            if isinstance(inner, dict):
                self._ingest_partial(inner)
            return
        if kind in {"content_block_start", "content_block_delta", "content_block_stop"}:
            self._ingest_partial(payload)
            return
        if kind == "assistant":
            self._ingest_assistant(payload.get("message") or {})
            return
        # Cursor's `--stream-partial-output` emits token deltas as its own
        # top-level `thinking` events rather than Anthropic content blocks.
        if kind == "thinking":
            if (payload.get("subtype") or "") == "completed":
                self._set_activity("")
                return
            text = payload.get("text")
            if isinstance(text, str) and text:
                self._saw_deltas = True
                self._add_text(text)
                self._set_activity("Thinking")
            return
        if kind == "item.completed":
            self._ingest_codex_item(payload.get("item") or {})
            return
        if kind == "result":
            self._set_activity("")

    def _ingest_codex_item(self, item: dict[str, Any]) -> None:
        """Make each completed Codex commentary/final message visible live.

        Codex emits both as ``agent_message`` items. Each message is a fresh
        user-facing update, not a delta, so the newest replaces the previous
        one. The reply extractor applies the same rule when the turn settles.
        """
        if item.get("type") != "agent_message":
            return
        text = item.get("text")
        if not isinstance(text, str) or not text.strip():  # py-org: allow-isinstance
            return
        self._answer = text
        self._trim()
        self._pending = True
        self._set_activity("Writing the reply")

    def _ingest_partial(self, event: dict[str, Any]) -> None:
        kind = event.get("type")
        if kind == "content_block_start":
            block = event.get("content_block") or {}
            block_type = str(block.get("type") or "")
            if block_type == "thinking":
                self._saw_deltas = True
                self._set_activity("Thinking")
            elif block_type == "tool_use":
                # Name only, and no arguments yet — they arrive as their own
                # deltas. The completed message carries both, so the step is
                # written from there; this just moves the header along.
                self._saw_deltas = True
                self._set_activity(str(block.get("name") or "Working"))
            elif block_type == "text":
                self._set_activity("Writing the reply")
            return

        if kind == "content_block_delta":
            delta = event.get("delta") or {}
            thinking = delta.get("thinking")
            if isinstance(thinking, str) and thinking:
                self._saw_deltas = True
                self._add_text(thinking)
            elif delta.get("type") == "text_delta":
                self._saw_deltas = True
                text = delta.get("text")
                if isinstance(text, str) and text:
                    self._answer += text
                    self._trim()
                    self._pending = True
                self._set_activity("Writing the reply")

    def _ingest_assistant(self, message: dict[str, Any]) -> None:
        """A completed assistant message.

        Tool calls are always taken from here — this is the first event that
        has their arguments, and "Read" says much less than "Read · runner.py".

        Reasoning is taken from here only when no deltas have been seen. With
        partial messages on, this event repeats text already streamed; with
        them off (an older CLI, or a turn that did not ask for them) it is the
        only source there is.
        """
        for block in message.get("content") or []:
            if not isinstance(block, dict):
                continue
            block_type = block.get("type")
            if block_type == "tool_use":
                self._add_event(_tool_label(str(block.get("name") or "tool"), block.get("input")))
            elif self._saw_deltas:
                continue
            elif block_type == "thinking":
                thinking = block.get("thinking")
                if isinstance(thinking, str) and thinking:
                    self._add_text(thinking)
                    self._set_activity("Thinking")
            elif block_type == "text":
                text = block.get("text")
                if isinstance(text, str) and text:
                    self._answer = text
                    self._pending = True
                self._set_activity("Writing the reply")

    # -- accumulation ------------------------------------------------------

    def _add_text(self, text: str) -> None:
        self._content += text
        self._trim()
        self._pending = True

    def _add_event(self, label: str) -> None:
        """A tool call, marked so the client can render it as a step, not prose."""
        clean = " ".join(label.split())[:MAX_ACTIVITY_CHARS]
        if not clean:
            return
        if not self._content:
            prefix = ""
        elif self._content.endswith("\n"):
            prefix = "\n"
        else:
            prefix = "\n\n"
        self._content += f"{prefix}· {clean}\n"
        self._trim()
        self._activity = clean
        self._pending = True

    def _set_activity(self, activity: str) -> None:
        if activity == self._activity:
            return
        self._activity = activity
        self._pending = True

    def _trim(self) -> None:
        if len(self._content) > MAX_THINKING_CHARS:
            self._content = "…" + self._content[-MAX_THINKING_CHARS:]
        if len(self._answer) > MAX_THINKING_CHARS:
            self._answer = self._answer[-MAX_THINKING_CHARS:]

    # -- delivery ----------------------------------------------------------

    def _maybe_flush(self, *, force: bool = False) -> None:
        if not self._pending:
            return
        if force or time.time() - self._last_flush >= FLUSH_INTERVAL_SECONDS:
            self._flush()

    def _flush(self) -> None:
        self._pending = False
        self._last_flush = time.time()
        self._seq += 1
        payload = {
            "turn_id": self.turn_id,
            "content": self._content,
            "answer": self._answer,
            "activity": self._activity,
            "seq": self._seq,
        }
        try:
            self._persist(payload)
        except Exception:
            # A thinking panel is an aid, never the work. A failed write must
            # not take down the turn that produced the text.
            logger.debug("Failed to persist thinking for turn %s", self.turn_id, exc_info=True)
        event_hub.publish(self._topic, {"type": "chat_thinking", "data": payload})

    def _persist(self, payload: dict[str, Any]) -> None:
        with Session(engine) as session:
            row = session.get(ChatTurnThinking, self.turn_id)
            if not row:
                row = ChatTurnThinking(turn_id=self.turn_id)
            row.content = payload["content"]
            row.answer = payload["answer"]
            row.activity = payload["activity"]
            row.seq = payload["seq"]
            row.updated_at = datetime.now(timezone.utc)
            session.add(row)
            session.commit()


def read_chat_turn_thinking(session: Session, turn_id: str) -> dict[str, Any]:
    """The turn's reasoning so far, or an empty frame if it has produced none."""
    row = session.get(ChatTurnThinking, turn_id)
    if not row:
        return {"turn_id": turn_id, "content": "", "answer": "", "activity": "", "seq": 0}
    return {
        "turn_id": row.turn_id,
        "content": row.content,
        "answer": row.answer,
        "activity": row.activity,
        "seq": row.seq,
    }


def finish_chat_turn_thinking(session: Session, turn_id: str) -> str:
    """Take the turn's transcript and drop the live row.

    Called as the turn settles, on the failure path as well as the success one:
    what an agent was doing when it died is the most useful thing it produced.
    """
    if not turn_id:
        return ""
    row = session.get(ChatTurnThinking, turn_id)
    if not row:
        return ""
    content = row.content
    session.delete(row)
    session.commit()
    event_hub.publish(chat_turn_topic(turn_id), {"type": "chat_thinking_done", "data": {}})
    return content.strip()


def with_thinking_part(parts_json: str, thinking: str) -> str:
    """Put the turn's reasoning at the front of its message's parts.

    First rather than last because the parts render in order and reasoning that
    reads after the conclusion it produced is backwards. Collapsed, because the
    conclusion is what the operator came for.
    """
    text = (thinking or "").strip()
    if not text:
        return parts_json
    try:
        parts = json.loads(parts_json or "[]")
    except json.JSONDecodeError:
        parts = []
    if not isinstance(parts, list):
        parts = []
    return json.dumps([ThinkingPart(content=text).model_dump(), *parts])


def clear_orphaned_chat_turn_thinking(session: Session) -> int:
    """Empty the table at startup. Returns how many rows went.

    Every settle path deletes its own row, so anything still here belongs to a
    turn the process died in the middle of — and the surfaces settle those same
    messages as failed at startup, so nothing is left to watch for this text.
    """
    orphaned = list(session.exec(select(ChatTurnThinking)).all())
    for row in orphaned:
        session.delete(row)
    if orphaned:
        session.commit()
    return len(orphaned)
