"""Register a detached child a chat turn started. Thin MCP wrapper around chat_wait.

Auto-approved: an inbox click would expire the turn. Orchestrated pipeline
agents are denied. The tool does not spawn the command — the agent wraps with
setsid and names the pid.
"""

from __future__ import annotations

import json
from typing import Any

from pydantic import ConfigDict, Field, ValidationError
from sqlmodel import Session, SQLModel

from loregarden.mcp.tool_ids import McpTool
from loregarden.services.chat_wait import register

TOOL_DEFINITIONS: list[dict[str, Any]] = [
    {
        "name": McpTool.REGISTER_CHAT_WAIT,
        "description": (
            "Register a detached child this chat turn started so the control plane "
            "resumes the thread when it exits. Wrap with "
            '`setsid sh -c \'(cmd > "$LOG" 2>&1; echo $? > "$LOG.rc")\'` and pass '
            "that pid, the command, the log path, and exactly one locator: ticket_id, "
            "chat_session_id, or workspace_slug+branch. The tool does not spawn the "
            "command. Auto-approved because an inbox click would expire the turn."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "pid": {
                    "type": "integer",
                    "exclusiveMinimum": 0,
                    "description": "Pid of the detached setsid child.",
                },
                "command": {
                    "type": "string",
                    "description": "The command the child is running.",
                },
                "log_path": {
                    "type": "string",
                    "description": "Path the child writes stdout/stderr to; exit code is {log_path}.rc.",
                },
                "ticket_id": {
                    "type": "string",
                    "description": "Ticket UUID for ticket-triage waits.",
                },
                "chat_session_id": {
                    "type": "string",
                    "description": "Home chat session id.",
                },
                "workspace_slug": {
                    "type": "string",
                    "description": "Workspace slug for branch-triage waits (with branch).",
                },
                "branch": {
                    "type": "string",
                    "description": "Git branch for branch-triage waits (with workspace_slug).",
                },
            },
            "required": ["pid", "command", "log_path"],
            "additionalProperties": False,
        },
    },
]


class _RegisterRequest(SQLModel):
    model_config = ConfigDict(extra="forbid")

    pid: int = Field(gt=0)
    command: str
    log_path: str
    ticket_id: str | None = None
    chat_session_id: str | None = None
    workspace_slug: str | None = None
    branch: str | None = None


def register_chat_wait(session: Session, arguments: dict[str, Any]) -> str:
    try:
        request = _RegisterRequest.model_validate(arguments)
    except ValidationError as exc:
        raise ValueError(
            f"invalid arguments for {McpTool.REGISTER_CHAT_WAIT.value}: {exc}"
        ) from exc
    result = register(
        session,
        pid=request.pid,
        command=request.command,
        log_path=request.log_path,
        ticket_id=request.ticket_id,
        chat_session_id=request.chat_session_id,
        workspace_slug=request.workspace_slug,
        branch=request.branch,
    )
    return json.dumps(result, indent=2)


HANDLERS = {McpTool.REGISTER_CHAT_WAIT.value: register_chat_wait}
