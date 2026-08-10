"""The `loregarden_check_organization` MCP tool: schema and handler.

Kept out of ``mcp/tools.py`` deliberately. That module is at its size cap and its
``execute_tool`` is a long if-chain over tool names; appending to both is how it
got that way. New tools register here-style instead — a definition plus a handler,
picked up through ``EXTENDED_TOOLS`` — so the chain stops growing.
"""

from __future__ import annotations

import json
from typing import Any

from sqlmodel import Session

from loregarden.mcp.tool_ids import McpTool
from loregarden.services.organization_gate_service import (
    OrganizationAction,
    OrganizationScope,
    run_organization_gate,
    workspace_for_slug,
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


def check_organization(session: Session, arguments: dict[str, Any]) -> str:
    action = OrganizationAction.try_parse(arguments.get("action") or OrganizationAction.CHECK.value)
    scope = OrganizationScope.try_parse(arguments.get("scope") or OrganizationScope.WORKTREE.value)
    if action is None or scope is None:
        raise ValueError("unknown action or scope for loregarden_check_organization")
    slug = str(arguments.get("workspace_slug") or "").strip()
    if not slug:
        raise ValueError("workspace_slug is required")
    workspace = workspace_for_slug(session, slug)
    return json.dumps(run_organization_gate(workspace, action, scope).as_payload(), indent=2)


#: Tools dispatched by table rather than by another `if name == ...` branch.
EXTENDED_TOOLS: dict[str, Any] = {McpTool.CHECK_ORGANIZATION.value: check_organization}
