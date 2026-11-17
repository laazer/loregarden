"""The `loregarden_check_organization` MCP tool: its schema.

The tool runs the `organization_check` operation
(`services/operations/organization_operations.py`), which shares the guarded
hook install with the page's workspace-integration route.
"""

from __future__ import annotations

from typing import Any

from loregarden.mcp.tool_ids import McpTool
from loregarden.services.organization_gate_service import (
    OrganizationAction,
    OrganizationScope,
)

TOOL_DEFINITION: dict[str, Any] = {
    "name": McpTool.CHECK_ORGANIZATION,
    "description": (
        "Run loregarden's organization guardrails against a workspace — the same "
        "checks the pre-commit hook and the stage transition gate run. Use before "
        "completing a stage to see what the gate will say, instead of spending a "
        "stage finding out. `action=check` reads the workspace's current changes; "
        "`hooks_status` reports whether its pre-commit hooks carry the managed "
        "block; `install_hooks` writes that block (it edits the target repo, so it "
        "goes through approval)."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "workspace_slug": {
                "type": "string",
                "description": "Workspace slug, e.g. loregarden.",
            },
            "action": {
                "type": "string",
                "description": "What to do (default check).",
                "enum": [action.value for action in OrganizationAction],
            },
            "scope": {
                "type": "string",
                "description": (
                    "Which diff to check (default worktree: an agent's edits are uncommitted)."
                ),
                "enum": [scope.value for scope in OrganizationScope],
            },
        },
        "required": ["workspace_slug"],
        "additionalProperties": False,
    },
}
