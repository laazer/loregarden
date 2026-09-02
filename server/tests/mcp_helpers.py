"""Calling MCP tools over the HTTP surface from tests.

Shared because two suites had grown identical copies, and the organization gate
is right that a third would be worse: the JSON-RPC envelope is the kind of
boilerplate that drifts silently — an `id` that stops matching, a `params` shape
that moves — and a copy that drifts still passes its own assertions.
"""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient


def call_mcp(client: TestClient, tool: str, args: dict[str, Any]) -> dict:
    """Invoke `tool` through /mcp and return the decoded JSON-RPC response.

    Asserts only the transport succeeded. A tool that refuses the call answers
    200 with an `error` member, which is a result the caller must inspect rather
    than a failure of this helper.
    """
    res = client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": tool, "arguments": args},
        },
    )
    assert res.status_code == 200, res.text
    return res.json()
