from fastapi import APIRouter, Depends, HTTPException
from loregarden.db.session import get_session
from loregarden.models.domain import McpServer, McpServerCreate, McpServerUpdate, McpServerView
from loregarden.services.mcp_health import check_server, record_health
from loregarden.services.mcp_registry import (
    McpRegistryError,
    create_server,
    delete_server,
    list_servers,
    to_view,
    update_server,
)
from loregarden.services.tool_telemetry import (
    counts_by_decision,
    counts_by_server,
    recent_calls,
)
from sqlmodel import Session

router = APIRouter(prefix="/mcp-servers", tags=["mcp-servers"])


@router.get("", response_model=list[McpServerView])
def get_mcp_servers(session: Session = Depends(get_session)) -> list[McpServerView]:
    return [to_view(server) for server in list_servers(session)]


@router.get("/telemetry", response_model=dict)
def get_mcp_telemetry(limit: int = 50, session: Session = Depends(get_session)) -> dict:
    """What agents asked for and how it was resolved.

    Deliberately no request rate or execution latency: the permission bridge
    sees the request and the decision, never the result, so those would be
    invented. `decision_ms` is the wait for a decision, which for a prompted
    call is how long the operator took.
    """
    return {
        "by_server": counts_by_server(session),
        "by_decision": counts_by_decision(session),
        "recent": [
            {
                "id": call.id,
                "run_id": call.run_id,
                "ticket_id": call.ticket_id,
                "agent_id": call.agent_id,
                "tool_name": call.tool_name,
                "server_name": call.server_name,
                "decision": call.decision,
                "decision_ms": call.decision_ms,
                "created_at": call.created_at.isoformat(),
            }
            for call in recent_calls(session, limit=limit)
        ],
    }


@router.post("", response_model=McpServerView)
def post_mcp_server(
    body: McpServerCreate, session: Session = Depends(get_session)
) -> McpServerView:
    try:
        return to_view(create_server(session, body))
    except McpRegistryError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.patch("/{server_id}", response_model=McpServerView)
def patch_mcp_server(
    server_id: str, body: McpServerUpdate, session: Session = Depends(get_session)
) -> McpServerView:
    try:
        return to_view(update_server(session, server_id, body))
    except McpRegistryError as exc:
        # "Server not found" is a 404; a rejected registration is a 400.
        status = 404 if str(exc) == "Server not found" else 400
        raise HTTPException(status, str(exc)) from exc


@router.delete("/{server_id}")
def remove_mcp_server(server_id: str, session: Session = Depends(get_session)) -> dict:
    try:
        delete_server(session, server_id)
    except McpRegistryError as exc:
        raise HTTPException(404, str(exc)) from exc
    return {"deleted": server_id}


@router.post("/{server_id}/health-check", response_model=McpServerView)
def check_mcp_server_health(
    server_id: str, session: Session = Depends(get_session)
) -> McpServerView:
    """Reach this server once and record what happened.

    Synchronous and operator-triggered. The check is bounded by its own timeout,
    so the worst case is a slow response rather than a hung request, and nothing
    about it touches the path an agent uses.
    """
    server = session.get(McpServer, server_id)
    if not server:
        raise HTTPException(404, "Server not found")
    return to_view(record_health(session, server, check_server(server)))
