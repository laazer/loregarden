"""MCP HTTP endpoint — mounted on the main Loregarden FastAPI app."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse
from loregarden.db.session import get_session
from loregarden.mcp.protocol import SERVER_INFO, handle_message
from sqlmodel import Session

router = APIRouter(tags=["mcp"])


@router.get("")
def mcp_info() -> dict[str, Any]:
    return {
        "service": "loregarden-mcp",
        "transport": "streamable-http",
        "serverInfo": SERVER_INFO,
        "usage": "POST JSON-RPC messages to this URL (initialize, tools/list, tools/call).",
    }


@router.post("")
async def mcp_post(
    request: Request,
    session: Session = Depends(get_session),
) -> JSONResponse:
    body = await request.json()
    # Set only by Loregarden's own CLI invocation builders for a run they supervise
    # (see agents/mcp_context.py) — a plain curl or an external_mcp-driven orchestrator
    # never sends it, so this covers the CLI-subprocess path only. See the
    # `orchestrated` docstring on mcp.tools.execute_tool for the known gap.
    orchestrated = request.headers.get("X-Loregarden-Orchestrated", "") == "1"
    # Off the event loop. `handle_message` is synchronous and a tool can run for
    # as long as it likes — `loregarden_start_orchestration` on the builtin
    # driver executes the whole orchestration before returning. Called inline
    # from this coroutine, that parked every other request on the server —
    # /health, the UI, the queue board, every agent's own MCP call — for the
    # life of the run. Measured: three 20-second timeouts on /health, 45
    # minutes into a run that was otherwise healthy (lg-milestone-that-776).
    result = await run_in_threadpool(handle_message, session, body, orchestrated=orchestrated)
    return JSONResponse(content=result)
