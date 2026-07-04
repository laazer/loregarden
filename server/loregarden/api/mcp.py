"""MCP HTTP endpoint — mounted on the main Loregarden FastAPI app."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from sqlmodel import Session

from loregarden.db.session import get_session
from loregarden.mcp.protocol import SERVER_INFO, handle_message

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
    result = handle_message(session, body)
    return JSONResponse(content=result)
