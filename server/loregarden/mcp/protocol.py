"""MCP JSON-RPC protocol handler."""

from __future__ import annotations

from typing import Any

from sqlmodel import Session

from loregarden.mcp.tools import TOOL_DEFINITIONS, execute_tool

SERVER_INFO = {"name": "loregarden", "version": "0.1.0"}
PROTOCOL_VERSION = "2024-11-05"


def handle_request(session: Session, req: dict[str, Any]) -> dict[str, Any] | None:
    method = req.get("method")
    req_id = req.get("id")

    if method == "initialize":
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"tools": {}},
                "serverInfo": SERVER_INFO,
            },
        }

    if method == "tools/list":
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {"tools": TOOL_DEFINITIONS},
        }

    if method == "tools/call":
        params = req.get("params") or {}
        name = params.get("name")
        arguments = params.get("arguments") or {}
        try:
            result = execute_tool(session, name, arguments)
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {"content": [{"type": "text", "text": result}]},
            }
        except Exception as exc:
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "error": {"code": -32000, "message": str(exc)},
            }

    if method == "notifications/initialized":
        return None

    if method == "ping":
        return {"jsonrpc": "2.0", "id": req_id, "result": {}}

    return {
        "jsonrpc": "2.0",
        "id": req_id,
        "error": {"code": -32601, "message": f"Method not found: {method}"},
    }


def handle_message(session: Session, body: Any) -> Any:
    """Accept a single JSON-RPC object or a batch array."""
    if isinstance(body, list):
        responses = []
        for item in body:
            if not isinstance(item, dict):
                continue
            resp = handle_request(session, item)
            if resp is not None:
                responses.append(resp)
        return responses
    if isinstance(body, dict):
        resp = handle_request(session, body)
        return resp if resp is not None else {}
    raise ValueError("Invalid MCP message body")
