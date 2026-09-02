"""Extract human-readable assistant text from CLI stdout."""

from __future__ import annotations

import json
from typing import Any

from loregarden.services.run_log_stream import format_stream_payload


def _result_text(payload: dict[str, Any]) -> str:
    result = payload.get("result")
    if isinstance(result, str) and result.strip():
        return result.strip()
    if isinstance(result, dict):
        text = result.get("text") or result.get("output")
        if text:
            return str(text).strip()
    return ""


def _codex_agent_message_text(item: Any) -> str:
    """Codex final assistant text arrives as item.completed / agent_message."""
    if not isinstance(item, dict):  # py-org: allow-isinstance
        return ""
    if item.get("type") != "agent_message":
        return ""
    text = item.get("text")
    if isinstance(text, str) and text.strip():  # py-org: allow-isinstance
        return text.strip()
    return ""


class _NdjsonReply:
    """Accumulates a reply across an NDJSON stream, one line at a time."""

    def __init__(self) -> None:
        self.parts: list[str] = []
        self.codex_reply = ""

    def ingest(self, payload: dict[str, Any]) -> str | None:
        """Feed one decoded line. A non-``None`` return settles the reply."""
        if payload.get("type") == "result":
            text = _result_text(payload)
            if text:
                return text
        if payload.get("type") == "item.completed":
            # Codex emits commentary updates and the final response as separate
            # complete messages. The last one is the reply; concatenating them
            # delays the update and pollutes the settled answer with progress text.
            codex_text = _codex_agent_message_text(payload.get("item"))
            if codex_text:
                self.codex_reply = codex_text
                return None
        formatted = format_stream_payload(payload)
        if formatted and formatted[0] == "OUT":
            self.parts.append(formatted[1])
        return None

    def settle(self) -> str | None:
        if self.codex_reply:
            return self.codex_reply
        if self.parts:
            return "\n".join(self.parts).strip()
        return None


def _extract_from_ndjson_lines(lines: list[str]) -> str | None:
    reply = _NdjsonReply()
    for line in lines:
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(payload, dict):  # py-org: allow-isinstance
            continue
        settled = reply.ingest(payload)
        if settled:
            return settled
    return reply.settle()


def _extract_from_single_json(raw: str) -> str | None:
    if not raw.startswith("{"):
        return None
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return raw
    if not isinstance(payload, dict):  # py-org: allow-isinstance
        return None
    if payload.get("type") == "result":
        text = _result_text(payload)
        if text:
            return text
    formatted = format_stream_payload(payload)
    if formatted and formatted[0] == "OUT":
        return formatted[1]
    return None


def extract_triage_reply(stdout: str) -> str:
    """Normalize stdout from triage CLIs (plain text or stream-json NDJSON)."""
    raw = stdout.strip()
    if not raw:
        return ""

    lines = [line.strip() for line in raw.splitlines() if line.strip()]
    if lines and all(line.startswith("{") for line in lines):
        reply = _extract_from_ndjson_lines(lines)
        if reply:
            return reply

    single = _extract_from_single_json(raw)
    if single is not None:
        return single

    return raw
