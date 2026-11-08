"""The merge queue's MCP tools (lg-milestone-that-971): one read, two gated writes.

Registered through `EXTENDED_TOOLS`, like every tool since `mcp/tools.py` hit its
size cap. They call `merge_queue_requests`, as the HTTP routes do, so an agent
and the board read and drive the queue one way.

Approval follows the action: `loregarden_read_merge_queue` is in
`READ_ONLY_MCP_TOOLS` and auto-approves; enqueue and dequeue are in no
auto-approved set, so on a run without the grant they reach the approvals
inbox. A refusal the queue gives on purpose raises with the queue's reason; a
GitHub read that fails raises too — neither comes back as an empty answer.
"""

from __future__ import annotations

import json
from typing import Any

from sqlmodel import Session, select

from loregarden.mcp.tool_ids import McpTool
from loregarden.mcp.tool_schemas import (
    boolean_prop,
    enum_string_prop,
    integer_prop,
    string_prop,
    tool_schema,
)
from loregarden.models.domain import MergeQueueAction, Workspace
from loregarden.services import merge_queue
from loregarden.services.merge_queue import Admission
from loregarden.services.merge_queue_requests import (
    admitted_or_refuse,
    enqueue_pull_request,
    enqueue_ticket,
    entry_detail,
    queue_view,
)
from loregarden.services.orchestration_callbacks import OrchestrationCallbackService
from loregarden.services.ticket_workspace_binding import workspace_for_ticket

TOOL_DEFINITIONS: list[dict[str, Any]] = [
    {
        "name": McpTool.MERGE_QUEUE_STATUS,
        "description": (
            "Read the merge queue: what is landing on the base branch, in order, and why the "
            "head entry is waiting (`wait`). Pass `entry_id` for one entry with the required "
            "checks it waits on, read live from GitHub for a testing entry. Read-only."
        ),
        "inputSchema": tool_schema(
            properties={
                "workspace_slug": string_prop("Workspace whose queue to read."),
                "entry_id": string_prop("One entry, with its checks, instead of the queue."),
                "live": boolean_prop(
                    "Read a testing entry's checks from GitHub now (default true); false "
                    "returns what the loop recorded on its last cycle."
                ),
            },
            required=[],
        ),
    },
    {
        "name": McpTool.ENQUEUE_MERGE,
        "description": (
            "Put an open pull request in the merge queue, which merges it onto the tip of "
            "main, waits for the required checks on that result, and lands exactly it. Use "
            "this instead of `gh pr merge --auto`. Pass `ticket_id` for a tree's PR (any "
            "ticket in the tree; refused when it has no open PR, so publish it with "
            "loregarden_land_ticket first), or `pr_number` plus `workspace_slug` for any "
            "other PR, such as an interactive session's branch. A PR's branch is its "
            "author's: the queue pushes its staged commit there, and adopts a push the "
            "author makes while it waits. A write: gated."
        ),
        "inputSchema": tool_schema(
            properties={
                "ticket_id": string_prop("Ticket UUID or external id, for a tree's PR."),
                "pr_number": integer_prop("Pull request number, for a PR with no ticket."),
                "workspace_slug": string_prop(
                    "Workspace; required with pr_number, or when ticket_id is an external id."
                ),
            },
            required=[],
        ),
    },
    {
        "name": McpTool.DEQUEUE_MERGE,
        "description": (
            "Withdraw a merge queue entry, or retry a failed, timed-out or withdrawn one (it "
            "goes to the back). Withdraw is refused once the entry is merging. A write: gated."
        ),
        "inputSchema": tool_schema(
            properties={
                "entry_id": string_prop("The entry, from loregarden_read_merge_queue."),
                "action": enum_string_prop(
                    "withdraw or retry.", [action.value for action in MergeQueueAction]
                ),
                "reason": string_prop("Why it is withdrawn; kept on the entry."),
            },
            required=["entry_id", "action"],
        ),
    },
]


def _dump(model: Any) -> str:
    return json.dumps(model.model_dump(mode="json"), indent=2)


def _read_merge_queue(session: Session, arguments: dict[str, Any]) -> str:
    entry_id = arguments.get("entry_id") or ""
    if entry_id:
        live = arguments.get("live")
        return _dump(entry_detail(session, entry_id, live=True if live is None else bool(live)))
    slug = arguments.get("workspace_slug") or ""
    if not slug:
        raise ValueError("Pass workspace_slug for the queue, or entry_id for one entry")
    workspace = session.exec(select(Workspace).where(Workspace.slug == slug)).first()
    if workspace is None:
        raise ValueError(f"No workspace {slug}")
    return _dump(queue_view(session, workspace))


def _admit(session: Session, arguments: dict[str, Any]) -> Admission:
    ticket_id = arguments.get("ticket_id") or ""
    pr_number = int(arguments.get("pr_number") or 0)
    slug = arguments.get("workspace_slug") or ""
    if bool(ticket_id) == bool(pr_number):
        raise ValueError("Pass exactly one of ticket_id or pr_number")
    if pr_number:
        if not slug:
            raise ValueError("Pass workspace_slug with pr_number")
        workspace = session.exec(select(Workspace).where(Workspace.slug == slug)).first()
        if workspace is None:
            raise ValueError(f"No workspace {slug}")
        return enqueue_pull_request(session, workspace, pr_number)
    ticket = OrchestrationCallbackService(session).resolve_ticket(
        ticket_id=ticket_id, workspace_slug=slug or None
    )
    workspace = workspace_for_ticket(session, ticket)
    if workspace is None:
        raise ValueError(f"{ticket.external_id} has no workspace")
    return enqueue_ticket(session, ticket, workspace)


def _enqueue_merge(session: Session, arguments: dict[str, Any]) -> str:
    admission = admitted_or_refuse(_admit(session, arguments))
    detail = entry_detail(session, admission.entry.id, live=False)
    return json.dumps(
        {
            "outcome": admission.outcome.value,
            "detail": admission.detail,
            "entry": detail.model_dump(mode="json"),
        },
        indent=2,
    )


def _dequeue_merge(session: Session, arguments: dict[str, Any]) -> str:
    action = MergeQueueAction.try_parse(arguments.get("action") or "")
    if action is None:
        raise ValueError("action must be withdraw or retry")
    entry_id = arguments["entry_id"]
    if action is MergeQueueAction.WITHDRAW:
        entry = merge_queue.withdraw(session, entry_id, reason=arguments.get("reason") or "")
    else:
        entry = merge_queue.retry(session, entry_id)
    return _dump(entry_detail(session, entry.id, live=False))


#: Registered in `tool_registry.EXTENDED_TOOLS`, not `execute_tool`'s chain.
HANDLERS = {
    McpTool.MERGE_QUEUE_STATUS.value: _read_merge_queue,
    McpTool.ENQUEUE_MERGE.value: _enqueue_merge,
    McpTool.DEQUEUE_MERGE.value: _dequeue_merge,
}
