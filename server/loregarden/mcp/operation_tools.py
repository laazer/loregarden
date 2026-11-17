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

from loregarden.mcp.operation_schemas import ToolShape, input_schema
from loregarden.mcp.tool_ids import McpTool
from loregarden.services.operations import Caller, OperationId, run_operation
from loregarden.services.operations.call import OPERATIONS
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


_RUN_KNOBS = frozenset(
    {
        "stop_at_stage_key",
        "auto_approve",
        "approve_design_plans",
        "auto_repair",
        "timeout_seconds",
        "slot_number",
    }
)

#: What each tool holds back from agents; a tool not named offers its whole model.
TOOL_SHAPES: Mapping[str, ToolShape] = {
    McpTool.GET_TICKET_BY_EXTERNAL.value: ToolShape(
        hidden=frozenset({"ticket_id"}), required=frozenset({"external_id", "workspace_slug"})
    ),
    # How a run is approved, repaired and timed is the operator's to choose.
    McpTool.START_ORCHESTRATION.value: ToolShape(
        hidden=_RUN_KNOBS, choices={"driver": ("builtin_autopilot", "external_mcp")}
    ),
    # The board's page of finished entries; an agent always gets STATUS_HISTORY.
    McpTool.MERGE_QUEUE_STATUS.value: ToolShape(hidden=frozenset({"limit"})),
    # A stage an agent completes always advances.
    McpTool.COMPLETE_STAGE.value: ToolShape(hidden=frozenset({"advance"})),
    # An agent attaches a file it wrote (content_json); the page's inline text is not offered.
    McpTool.ATTACH_ARTIFACT.value: ToolShape(
        hidden=frozenset({"content"}), required=frozenset({"kind", "title"})
    ),
    # An agent ends its own run; queued and running are not ends.
    McpTool.COMPLETE_ORCHESTRATION.value: ToolShape(
        choices={"status": ("succeeded", "failed", "blocked", "cancelled")}
    ),
    # An agent proposes a schedule (loregarden_propose_initiative_schedule) for a
    # person to approve; it never sets stops or a schedule itself.
    McpTool.SET_INITIATIVE_AUTOPILOT.value: ToolShape(hidden=frozenset({"stops", "schedule"})),
    # A requeue names why, and puts the ticket back in line rather than finishing it.
    McpTool.REQUEUE_TICKET.value: ToolShape(
        required=frozenset({"reason"}), choices={"state": ("backlog", "in_progress")}
    ),
    # An agent asks for a continuation or a scoped pass; the other modes are the page's.
    McpTool.SURVEY_QUEUE.value: ToolShape(choices={"mode": ("continuation", "scoped")}),
    McpTool.CREATE_MEMORY_RELATION.value: ToolShape(required=frozenset({"workspace_slug"})),
    # An imported issue is work, never an initiative.
    McpTool.SYNC_GITHUB_ISSUES.value: ToolShape(
        choices={"import_work_item_type": ("milestone", "feature", "capability", "task", "bug")}
    ),
    McpTool.CREATE_TICKET.value: ToolShape(
        hidden=frozenset({"milestone"}), required=frozenset({"title"})
    ),
    # The stage cursor, branch and git settings move through
    # loregarden_set_ticket_workflow or the page, not a content edit.
    McpTool.UPDATE_TICKET.value: ToolShape(
        hidden=frozenset(
            {
                "branch",
                "checked_acceptance_criteria",
                "compatibility_posture",
                "git_automation",
                "stage_key",
                "stage_status",
                "stage_updates",
                "workflow_stage_key",
                "workflow_stage_status",
                "workflow_template_slug",
            }
        )
    ),
}
_WHOLE_MODEL = ToolShape()


def operation_tool_schema(name: str) -> dict[str, Any]:
    """`name`'s input schema: its operation's args model, less its `ToolShape`."""
    operation = OPERATIONS[OPERATION_TOOLS[name]]
    return input_schema(operation.args, TOOL_SHAPES.get(name, _WHOLE_MODEL))


def _refuse_outside_shape(name: str, arguments: Mapping[str, Any]) -> None:
    """Hold a call to what the tool's schema offers, whoever skipped the schema."""
    shape = TOOL_SHAPES.get(name, _WHOLE_MODEL)
    offered = sorted(shape.hidden & arguments.keys())
    if offered:
        raise ValueError(f"{name} does not take {', '.join(offered)}")
    missing = sorted(key for key in shape.required if arguments.get(key) is None)
    if missing:
        raise ValueError(f"{name} requires {', '.join(missing)}")
    for key, allowed in shape.choices.items():
        value = arguments.get(key)
        # Blank is not a choice: the normalizers send "" for an omitted field.
        if value not in (None, "") and value not in allowed:
            raise ValueError(f"{name}: {key} must be one of {', '.join(allowed)}, not {value!r}")


def execute_operation_tool(
    session: Session, name: str, arguments: dict[str, Any], caller: Caller
) -> str | None:
    """The tool's reply as JSON, or None when `name` is not an operation's tool."""
    operation_id = OPERATION_TOOLS.get(name)
    if operation_id is None:
        return None
    _refuse_outside_shape(name, arguments)
    try:
        result = run_operation(session, operation_id, arguments, caller)
    except WorkQueued as queued:
        # Not an error: the work waits its turn and will run.
        result = queued.as_dict()
    return json.dumps(to_jsonable_python(result), indent=2)
