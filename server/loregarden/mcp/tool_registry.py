"""Tools dispatched by table instead of another branch in `execute_tool`.

``execute_tool`` is a long if-chain over tool names, already past the complexity
cap — so every tool appended to it makes the next one harder to add, and the
gates now say so. This is the seam out: a tool that needs only a session and its
arguments registers a handler here and lives in its own module.

``search_prior_work`` moved here as the first resident. It never needed the run
and ticket context the chain resolves midway, and moving it kept the chain's
branch count flat while `check_organization` was added.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from sqlmodel import Session

from loregarden.mcp.organization_tool import check_organization
from loregarden.mcp.tool_ids import McpTool
from loregarden.services.prior_work import search_prior_work

ToolHandler = Callable[[Session, dict[str, Any]], str]


def _search_prior_work(session: Session, arguments: dict[str, Any]) -> str:
    return json.dumps(
        {
            "results": search_prior_work(
                session,
                arguments["query"],
                workspace_slug=arguments.get("workspace_slug", ""),
                exclude_ticket_id=arguments.get("ticket_id", ""),
            )
        },
        indent=2,
    )


EXTENDED_TOOLS: dict[str, ToolHandler] = {
    McpTool.CHECK_ORGANIZATION.value: check_organization,
    McpTool.SEARCH_PRIOR_WORK.value: _search_prior_work,
}
