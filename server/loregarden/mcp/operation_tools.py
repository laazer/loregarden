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
from loregarden.services.work_admission import WorkQueued

#: Keyed by the wire name, as `tool_registry.EXTENDED_TOOLS` is.
OPERATION_TOOLS: Mapping[str, OperationId] = {
    McpTool.GET_TICKET.value: OperationId.TICKET_BRIEF,
    McpTool.GET_TICKET_BY_EXTERNAL.value: OperationId.TICKET_BRIEF,
    McpTool.LIST_TICKETS.value: OperationId.TICKET_SEARCH,
    McpTool.LIST_ARTIFACTS.value: OperationId.ARTIFACT_INDEX,
    McpTool.START_ORCHESTRATION.value: OperationId.ORCHESTRATION_START,
    McpTool.START_STAGE.value: OperationId.STAGE_START,
    McpTool.COMPLETE_STAGE.value: OperationId.STAGE_COMPLETE,
    McpTool.SKIP_STAGE.value: OperationId.STAGE_SKIP,
    McpTool.BLOCK_TICKET.value: OperationId.TICKET_BLOCK,
    McpTool.ATTACH_ARTIFACT.value: OperationId.ARTIFACT_ATTACH,
    McpTool.REQUEST_APPROVAL.value: OperationId.APPROVAL_REQUEST,
    McpTool.COMPLETE_ORCHESTRATION.value: OperationId.ORCHESTRATION_COMPLETE,
    McpTool.LINK_DEPENDENCY.value: OperationId.TICKET_DEPENDENCY_ADD,
    McpTool.UNLINK_DEPENDENCY.value: OperationId.TICKET_DEPENDENCY_REMOVE,
    McpTool.LINK_RELATION.value: OperationId.TICKET_RELATION_ADD,
    McpTool.UNLINK_RELATION.value: OperationId.TICKET_RELATION_REMOVE,
}


def execute_operation_tool(
    session: Session, name: str, arguments: dict[str, Any], caller: Caller
) -> str | None:
    """The tool's reply as JSON, or None when `name` is not an operation's tool."""
    operation_id = OPERATION_TOOLS.get(name)
    if operation_id is None:
        return None
    try:
        result = run_operation(session, operation_id, arguments, caller)
    except WorkQueued as queued:
        # Not an error: the work waits its turn and will run.
        result = queued.as_dict()
    return json.dumps(to_jsonable_python(result), indent=2)
