"""The MCP servers this control plane knows about, and how agents reach them.

Agents could reach exactly one server — loregarden's own, hardcoded into the
`--mcp-config` payload. Anything else had to be configured outside the control
plane, where nothing could see, audit, or disable it.

Registering a server here composes it into that payload, so the registry is the
one place a server is added or taken away rather than a table the UI reads and
nothing acts on.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone

from loregarden.models.domain import McpServer, McpServerCreate, McpServerUpdate, McpServerView
from loregarden.services.tool_policy import TOOL_POLICIES
from sqlmodel import Session, select

#: Transports the CLI config understands. A stdio server is launched by the CLI
#: itself; an http one is dialled.
TRANSPORTS = ("http", "stdio")


class McpRegistryError(ValueError):
    """A registration the registry will not accept."""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_args(raw: str) -> list[str]:
    try:
        parsed = json.loads(raw or "[]")
    except (TypeError, ValueError):
        return []
    return [str(item) for item in parsed] if isinstance(parsed, list) else []


def to_view(server: McpServer) -> McpServerView:
    return McpServerView(
        id=server.id,
        name=server.name,
        description=server.description,
        transport=server.transport,
        url=server.url,
        command=server.command,
        args=_parse_args(server.args_json),
        auth_env_var=server.auth_env_var,
        enabled=server.enabled,
        tool_policy=server.tool_policy,
        # Presence only. The value never leaves the process it is read in.
        auth_present=bool(server.auth_env_var and os.environ.get(server.auth_env_var)),
        created_at=server.created_at,
        updated_at=server.updated_at,
    )


def _validate(*, name: str, transport: str, url: str, command: str, tool_policy: str) -> None:
    if not name.strip():
        raise McpRegistryError("Server name is required")
    if transport not in TRANSPORTS:
        raise McpRegistryError(f"Unknown transport: {transport}")
    if tool_policy not in TOOL_POLICIES:
        raise McpRegistryError(f"Unknown tool policy: {tool_policy}")
    # A server missing the field its transport needs would register cleanly and
    # then fail inside an agent subprocess, where the cause is hard to see.
    if transport == "http" and not url.strip():
        raise McpRegistryError("An http server needs a url")
    if transport == "stdio" and not command.strip():
        raise McpRegistryError("A stdio server needs a command")


def list_servers(session: Session) -> list[McpServer]:
    return list(session.exec(select(McpServer).order_by(McpServer.name)).all())


def create_server(session: Session, body: McpServerCreate) -> McpServer:
    _validate(
        name=body.name,
        transport=body.transport,
        url=body.url,
        command=body.command,
        tool_policy=body.tool_policy,
    )
    if session.exec(select(McpServer).where(McpServer.name == body.name.strip())).first():
        raise McpRegistryError(f"A server named '{body.name.strip()}' is already registered")

    server = McpServer(
        name=body.name.strip(),
        description=body.description,
        transport=body.transport,
        url=body.url.strip(),
        command=body.command.strip(),
        args_json=json.dumps(list(body.args)),
        auth_env_var=body.auth_env_var.strip(),
        enabled=body.enabled,
        tool_policy=body.tool_policy,
    )
    session.add(server)
    session.commit()
    session.refresh(server)
    return server


def update_server(session: Session, server_id: str, body: McpServerUpdate) -> McpServer:
    server = session.get(McpServer, server_id)
    if not server:
        raise McpRegistryError("Server not found")

    if body.name is not None:
        clash = session.exec(select(McpServer).where(McpServer.name == body.name.strip())).first()
        if clash and clash.id != server.id:
            raise McpRegistryError(f"A server named '{body.name.strip()}' is already registered")
        server.name = body.name.strip()
    if body.description is not None:
        server.description = body.description
    if body.transport is not None:
        server.transport = body.transport.strip()
    if body.url is not None:
        server.url = body.url.strip()
    if body.command is not None:
        server.command = body.command.strip()
    if body.auth_env_var is not None:
        server.auth_env_var = body.auth_env_var.strip()
    if body.args is not None:
        server.args_json = json.dumps(list(body.args))
    if body.enabled is not None:
        server.enabled = body.enabled
    if body.tool_policy is not None:
        server.tool_policy = body.tool_policy.strip()

    _validate(
        name=server.name,
        transport=server.transport,
        url=server.url,
        command=server.command,
        tool_policy=server.tool_policy,
    )
    server.updated_at = _now()
    session.add(server)
    session.commit()
    session.refresh(server)
    return server


def delete_server(session: Session, server_id: str) -> None:
    server = session.get(McpServer, server_id)
    if not server:
        raise McpRegistryError("Server not found")
    session.delete(server)
    session.commit()


def cli_server_entries(session: Session) -> dict[str, dict]:
    """Registered servers as `mcpServers` entries for a CLI config.

    Disabled servers are withheld rather than removed, so a server that is
    misbehaving can be parked without losing how it was configured.

    A credential is resolved from the environment here, at the moment the
    subprocess is being built, and is never read from the database.
    """
    entries: dict[str, dict] = {}
    for server in list_servers(session):
        if not server.enabled:
            continue
        if server.transport == "http":
            entry: dict = {"type": "http", "url": server.url}
            token = os.environ.get(server.auth_env_var, "") if server.auth_env_var else ""
            if token:
                entry["headers"] = {"Authorization": f"Bearer {token}"}
        else:
            entry = {
                "type": "stdio",
                "command": server.command,
                "args": _parse_args(server.args_json),
            }
            if server.auth_env_var:
                # Pass the variable through by name; the child reads it itself.
                entry["env"] = {server.auth_env_var: os.environ.get(server.auth_env_var, "")}
        entries[server.name] = entry
    return entries
