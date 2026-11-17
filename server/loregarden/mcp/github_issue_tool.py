"""The `loregarden_sync_github_issues` MCP tool: its schema.

The tool runs the `github_issues` operation
(`services/operations/github_issue_operations.py`), which shares each action's
function with the matching route; this module holds the schema.
"""

from __future__ import annotations

from typing import Any

from loregarden.mcp.tool_ids import McpTool
from loregarden.models.domain import (
    ConflictPolicy,
    WorkItemType,
)
from loregarden.services.operations.github_issue_operations import GithubIssueAction

TOOL_DEFINITION: dict[str, Any] = {
    "name": McpTool.SYNC_GITHUB_ISSUES,
    "description": (
        "Two-way sync between tickets and GitHub issues (title, body/description, "
        "and open/closed — done is 'closed as completed', wont_do is 'closed as not "
        "planned'). Each field moves in whichever direction changed since the last "
        "sync; a field changed on both sides is reported as a conflict and left alone "
        "unless policy is local or remote. Actions: status (read a ticket's link), "
        "publish (open an issue for a ticket), sync (one ticket), sync_workspace (every "
        "linked ticket; with import_parent_ticket_id, also import unlinked open issues "
        "as children of that ticket). Uses the gh CLI's credentials."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": [a.value for a in GithubIssueAction],
                "description": "What to do.",
            },
            "ticket_id": {
                "type": "string",
                "description": "Ticket id — for status, publish and sync.",
            },
            "workspace_slug": {
                "type": "string",
                "description": "Workspace slug — for sync_workspace.",
            },
            "policy": {
                "type": "string",
                "enum": [p.value for p in ConflictPolicy],
                "description": "Conflicting fields: report (default), local wins, remote wins.",
            },
            "import_parent_ticket_id": {
                "type": "string",
                "description": "sync_workspace: import unlinked open issues under this ticket.",
            },
            "import_work_item_type": {
                "type": "string",
                "enum": [t.value for t in WorkItemType if t is not WorkItemType.INITIATIVE],
                "description": "sync_workspace: type for imported tickets (default bug).",
            },
            "import_label": {
                "type": "string",
                "description": "sync_workspace: only import issues with this label.",
            },
        },
        "required": ["action"],
        "additionalProperties": False,
    },
}
