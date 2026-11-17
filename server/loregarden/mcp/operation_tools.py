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
    # The merge queue (`services/operations/merge_queue_ops.py`).
    McpTool.MERGE_QUEUE_STATUS.value: OperationId.MERGE_QUEUE_READ,
    McpTool.ENQUEUE_MERGE.value: OperationId.MERGE_QUEUE_ENQUEUE,
    McpTool.DEQUEUE_MERGE.value: OperationId.MERGE_QUEUE_ENTRY_ACTION,
    McpTool.ADD_INITIATIVE_MEMBER.value: OperationId.INITIATIVE_MEMBER_ADD,
    McpTool.REMOVE_INITIATIVE_MEMBER.value: OperationId.INITIATIVE_MEMBER_REMOVE,
    McpTool.GET_INITIATIVE_PLAN.value: OperationId.INITIATIVE_PLAN,
    McpTool.SET_INITIATIVE_AUTOPILOT.value: OperationId.PLAN_AUTOPILOT_SET,
    McpTool.START_INITIATIVE_WORK.value: OperationId.INITIATIVE_WORK_START,
    McpTool.MARK_NEEDS_PERSON.value: OperationId.INITIATIVE_NEEDS_PERSON,
    # Near-twins unified to the stricter rule.
    McpTool.REQUEUE_TICKET.value: OperationId.TICKET_REQUEUE,
    McpTool.SURVEY_QUEUE.value: OperationId.SURVEY_QUEUE,
    McpTool.RESEARCH_RECORD.value: OperationId.RESEARCH_RECORD,
    # Memory, docker capacity, disk, GitHub issues, organization.
    McpTool.MEMORY_STATUS.value: OperationId.MEMORY_STATUS,
    McpTool.CREATE_MEMORY_RELATION.value: OperationId.MEMORY_RELATION_CREATE,
    McpTool.DOCKER_CAPACITY_STATUS.value: OperationId.DOCKER_CAPACITY_STATUS,
    McpTool.FORCE_RELEASE_DOCKER_LEASE.value: OperationId.DOCKER_LEASE_FORCE_RELEASE,
    McpTool.DISK_USAGE.value: OperationId.DISK_USAGE,
    McpTool.SYNC_GITHUB_ISSUES.value: OperationId.GITHUB_ISSUES,
    McpTool.CHECK_ORGANIZATION.value: OperationId.ORGANIZATION_CHECK,
    # Filing and editing a ticket, and merging its pull request.
    McpTool.CREATE_TICKET.value: OperationId.TICKET_CREATE,
    McpTool.UPDATE_TICKET.value: OperationId.TICKET_UPDATE,
    McpTool.SET_TICKET_WORKFLOW.value: OperationId.TICKET_WORKFLOW_SET,
    McpTool.MERGE_PULL_REQUEST.value: OperationId.TICKET_PULL_REQUEST_MERGE,
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
