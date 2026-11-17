"""Add a ticket to an initiative, or take it out, without re-parenting it.

Own module beside `initiative_plan_tools`, which is the planner's floor; these
change what an initiative covers, so they stay gated like any ticket write.
Both run as operations (`services/operations/initiatives.py`); this holds the
schemas agents read.
"""

from __future__ import annotations

from typing import Any

from loregarden.mcp.tool_ids import McpTool

TOOL_DEFINITIONS: list[dict[str, Any]] = [
    {
        "name": McpTool.ADD_INITIATIVE_MEMBER,
        "description": (
            "Track a ticket (any type but an initiative) and its whole subtree in an "
            "initiative's view, plan, forecast and autopilot, WITHOUT re-parenting it: it "
            "keeps its parent, milestone, workspace and integration branch. Refused when the "
            "initiative already covers it (as a member, a child, or under one), when it "
            "contains one of the initiative's members, or when it is an initiative. A ticket "
            "may belong to several initiatives. Returns the initiative's top-level tickets."
        ),
    },
    {
        "name": McpTool.REMOVE_INITIATIVE_MEMBER,
        "description": (
            "Stop an initiative tracking a member ticket. The ticket itself is not changed; "
            "a child the initiative parents is detached through update_ticket instead."
        ),
    },
]
