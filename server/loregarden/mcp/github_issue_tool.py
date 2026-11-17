"""The `loregarden_sync_github_issues` MCP tool: its schema.

The tool runs the `github_issues` operation
(`services/operations/github_issue_operations.py`), which shares each action's
function with the matching route; this module holds the schema.
"""

from __future__ import annotations

from typing import Any

from loregarden.mcp.tool_ids import McpTool

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
}
