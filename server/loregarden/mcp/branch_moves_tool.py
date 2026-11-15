"""The `loregarden_branch_moves` MCP tool: who moved a branch, when, and why.

Reads the ledger `integration_branch_moves.move_integration_branch` writes on
every move, newest first. Read-only. The question it exists for is the one
lg-milestone-that-1316's loss could not answer, because the branch's reflog
went with the branch: "which writer moved `integration/…` off that commit?"
"""

from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator
from sqlmodel import Session

from loregarden.mcp.tool_ids import McpTool
from loregarden.services.integration_branch_moves import branch_moves
from loregarden.services.organization_gate_service import workspace_for_slug

TOOL_DEFINITION: dict[str, Any] = {
    "name": McpTool.BRANCH_MOVES,
    "description": (
        "Who moved a branch the control plane owns (an integration branch, or one a "
        "merge turn moved): every move, creation and deletion, newest first, with both "
        "tips, the writer (module.function), the reason, the run, and the UTC time. "
        "The record outlives the branch and its reflog. Read-only. Pass `branch` for one "
        "branch's history."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "workspace_slug": {"type": "string", "description": "Workspace slug, e.g. loregarden."},
            "branch": {
                "type": "string",
                "description": "Full branch name, e.g. integration/lg-milestone-that-491.",
            },
            "limit": {
                "type": "integer",
                "minimum": 1,
                "maximum": 500,
                "description": "Most recent moves to return. Default 50.",
            },
        },
        "required": ["workspace_slug"],
        "additionalProperties": False,
    },
}


class BranchMovesRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workspace_slug: str
    branch: str = ""
    limit: int = Field(default=50, ge=1, le=500)

    @field_validator("workspace_slug")
    @classmethod
    def _required(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("workspace_slug is required")
        return value.strip()


def branch_moves_tool(session: Session, arguments: dict[str, Any]) -> str:
    try:
        request = BranchMovesRequest.model_validate(arguments)
    except ValidationError as exc:
        raise ValueError(f"invalid arguments for {McpTool.BRANCH_MOVES.value}: {exc}") from exc
    workspace = workspace_for_slug(session, request.workspace_slug)
    moves = branch_moves(session, workspace, branch=request.branch, limit=request.limit)
    return json.dumps(
        {
            "workspace_slug": request.workspace_slug,
            "branch": request.branch,
            "count": len(moves),
            "moves": [move.model_dump(mode="json", exclude={"workspace_id"}) for move in moves],
        },
        indent=2,
    )
