"""POST /mcp must not run a tool on the event loop.

A tool can take as long as it likes — `loregarden_start_orchestration` on the
builtin driver runs the whole orchestration before returning. Called inline
from the async handler, that parked every other request on the server for the
life of the run: /health, the UI, every agent's own MCP call.
"""

from __future__ import annotations

import asyncio
import time
from unittest import mock

import httpx
from loregarden.api import mcp as mcp_api
from loregarden.main import app


async def test_a_slow_tool_does_not_park_the_rest_of_the_server():
    def slow_tool(session, body, *, orchestrated):
        time.sleep(1.0)  # a synchronous tool, the way every tool is
        return {"jsonrpc": "2.0", "id": body.get("id"), "result": {"slow": True}}

    transport = httpx.ASGITransport(app=app)
    with mock.patch.object(mcp_api, "handle_message", side_effect=slow_tool):
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            started = time.monotonic()
            slow = asyncio.create_task(
                client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
            )
            await asyncio.sleep(0.05)  # let the slow request get in first
            health = await client.get("/health")
            health_at = time.monotonic() - started
            response = await slow
            slow_at = time.monotonic() - started

    assert health.status_code == 200
    # Ordering, not a wall-clock budget: under load the handoff itself can take
    # a few hundred ms, but only the inline call makes /health wait for the
    # whole tool. Without the fix /health lands *after* the tool returns.
    assert health_at < slow_at, (
        f"/health answered at {health_at:.2f}s, after the 1s tool at {slow_at:.2f}s"
    )
    assert slow_at - health_at > 0.3, "the tool was still running when /health answered"
    assert response.json()["result"] == {"slow": True}
