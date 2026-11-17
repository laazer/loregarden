"""The merge queue's MCP tools (lg-milestone-that-971): one read, two gated writes.

Their schemas only: each tool is an operation (`services/operations/merge_queue_ops.py`,
bound in `mcp/operation_tools.py`), which the HTTP routes run too, so an agent
and the board read and drive the queue one way.

Approval follows the action: `loregarden_read_merge_queue` is in
`READ_ONLY_MCP_TOOLS` and auto-approves; enqueue and dequeue are in no
auto-approved set, so on a run without the grant they reach the approvals
inbox. A refusal the queue gives on purpose raises with the queue's reason; a
GitHub read that fails raises too — neither comes back as an empty answer.
"""

from __future__ import annotations

from typing import Any

from loregarden.mcp.tool_ids import McpTool

TOOL_DEFINITIONS: list[dict[str, Any]] = [
    {
        "name": McpTool.MERGE_QUEUE_STATUS,
        "description": (
            "Read the merge queue: what is landing on the base branch, in order, and why the "
            "head entry is waiting (`wait`). `ahead` is an entry's place in line (live entries "
            "before it); `position` is a lifetime sequence number, not a place in line. Lists "
            "the live entries, then the latest finished ones. Pass `entry_id` for one entry with the required "
            "checks it waits on, read live from GitHub for a testing entry. Read-only."
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
    },
    {
        "name": McpTool.DEQUEUE_MERGE,
        "description": (
            "Withdraw a merge queue entry, or retry a failed, timed-out or withdrawn one (it "
            "goes to the back). Withdraw is refused once the entry is merging. A write: gated."
        ),
    },
]
