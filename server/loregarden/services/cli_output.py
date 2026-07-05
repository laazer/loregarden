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


def extract_triage_reply(stdout: str) -> str:
    """Normalize stdout from triage CLIs (plain text or stream-json NDJSON)."""
    raw = stdout.strip()
    if not raw:
        return ""

    lines = [line.strip() for line in raw.splitlines() if line.strip()]
    if lines and all(line.startswith("{") for line in lines):
        parts: list[str] = []
        for line in lines:
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(payload, dict):
                continue
            if payload.get("type") == "result":
                text = _result_text(payload)
                if text:
                    return text
            formatted = format_stream_payload(payload)
            if formatted and formatted[0] == "OUT":
                parts.append(formatted[1])
        if parts:
            return "\n".join(parts).strip()

    if raw.startswith("{"):
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            return raw
        if isinstance(payload, dict):
            if payload.get("type") == "result":
                text = _result_text(payload)
                if text:
                    return text
            formatted = format_stream_payload(payload)
            if formatted and formatted[0] == "OUT":
                return formatted[1]

    return raw
