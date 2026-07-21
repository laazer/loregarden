from fastapi import APIRouter, Depends, HTTPException
from loregarden.db.session import get_session
from loregarden.models.domain import McpServerCreate, McpServerUpdate, McpServerView
from loregarden.services.mcp_registry import (
    McpRegistryError,
    create_server,
    delete_server,
    list_servers,
    to_view,
    update_server,
)
from sqlmodel import Session

router = APIRouter(prefix="/mcp-servers", tags=["mcp-servers"])


@router.get("", response_model=list[McpServerView])
def get_mcp_servers(session: Session = Depends(get_session)) -> list[McpServerView]:
    return [to_view(server) for server in list_servers(session)]


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
