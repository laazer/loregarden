"""MCP tools that are operations: the tool name is MCP's spelling of one.

`execute_tool` tries this table before its own chain, so a tool listed here
runs through `operation_call` on MCP and on the CLI alike.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from pydantic_core import to_jsonable_python
from sqlmodel import Session

from loregarden.mcp.tool_ids import McpTool
from loregarden.services.operations import Caller, OperationId, run_operation

#: Keyed by the wire name, as `tool_registry.EXTENDED_TOOLS` is.
OPERATION_TOOLS: Mapping[str, OperationId] = {
    McpTool.GET_TICKET.value: OperationId.TICKET_BRIEF,
    McpTool.GET_TICKET_BY_EXTERNAL.value: OperationId.TICKET_BRIEF,
}


def execute_operation_tool(
    session: Session, name: str, arguments: dict[str, Any], caller: Caller
) -> str | None:
    """The tool's reply as JSON, or None when `name` is not an operation's tool."""
    operation_id = OPERATION_TOOLS.get(name)
    if operation_id is None:
        return None
    result = run_operation(session, operation_id, arguments, caller)
    return json.dumps(to_jsonable_python(result), indent=2)
