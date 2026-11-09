"""The Claude/Cursor stream-json protocol: reading the CLI's lines, writing its replies.

Pure functions over the messages themselves, apart from the permission bridge
that drives a run with them.
"""

from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger(__name__)


def parse_ndjson_line(line: str) -> dict[str, Any] | None:
    line = line.strip()
    if not line:
        return None
    try:
        payload = json.loads(line)
    except json.JSONDecodeError:
        return None
    # A CLI line may be any JSON value; only an object is a stream-json message.
    is_message = isinstance(payload, dict)  # py-org: allow-isinstance - foreign CLI output
    return payload if is_message else None


def build_control_response(
    *,
    request_id: str,
    approved: bool,
    message: str = "",
    updated_input: dict[str, Any] | None = None,
) -> dict[str, Any]:
    inner: dict[str, Any]
    if approved:
        inner = {"behavior": "allow"}
        # Empty updatedInput overwrites Claude's original tool args and breaks Bash/MCP.
        if updated_input:
            inner["updatedInput"] = updated_input
    else:
        inner = {"behavior": "deny", "message": message or "Denied via Loregarden inbox"}
    return {
        "type": "control_response",
        "response": {
            "subtype": "success",
            "request_id": request_id,
            "response": inner,
        },
    }


def build_user_message(prompt: str, *, session_id: str | None = None) -> dict[str, Any]:
    message: dict[str, Any] = {
        "type": "user",
        "message": {"role": "user", "content": prompt},
        "parent_tool_use_id": None,
    }
    if session_id:
        message["session_id"] = session_id
    return message


def result_payload_status(payload: dict[str, Any]) -> tuple[bool, bool]:
    """Return (finished, failed) for Claude/Cursor stream-json result events."""
    if payload.get("type") != "result":
        return False, False
    failed = bool(payload.get("is_error")) or payload.get("subtype") == "error"
    return True, failed


def result_failure_text(payload: dict[str, Any]) -> str:
    """What a failed stream-json result says went wrong, or "" when it says nothing.

    The Claude CLI reports its own failures here (an expired or missing login
    is "Invalid API key · Please run /login") and writes nothing to stderr, so
    without this a failed run reaches the operator as a bare "Agent run failed".
    """
    result = payload.get("result")
    if isinstance(result, str) and result.strip():  # py-org: allow-isinstance - foreign CLI payload
        return result.strip()
    errors = payload.get("errors")
    if isinstance(errors, list):  # py-org: allow-isinstance - foreign CLI payload
        text = "; ".join(str(error).strip() for error in errors if str(error).strip())
        if text:
            return text
    subtype = payload.get("subtype")
    return f"result {subtype}" if subtype else ""


def serialize_tool_input(tool_input: Any) -> str:
    """Persist full tool input JSON without truncation."""
    return json.dumps(tool_input, ensure_ascii=False)


def parse_stored_tool_input(raw: str) -> dict[str, Any]:
    """Parse stored tool input; tolerate legacy truncated payloads."""
    if not raw or raw == "{}":
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        # An unparseable payload becomes an empty tool input, which is what the
        # approver sees and what an always-allow rule would be written from.
        logger.exception("Stored tool input is not valid JSON; treating as empty input")
        return {}
    is_object = isinstance(parsed, dict)  # py-org: allow-isinstance - stored CLI tool input
    return parsed if is_object else {}
