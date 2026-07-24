"""MCP HTTP endpoint — mounted on the main Loregarden FastAPI app."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request
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
    result = handle_message(session, body, orchestrated=orchestrated)
    return JSONResponse(content=result)
