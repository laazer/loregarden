"""The `loregarden_search_reference` MCP tool: schema and handler.

Its own module, like `reference_tool`, because `mcp/tools.py` is at its size cap
and `execute_tool` past its complexity cap. Registered through `EXTENDED_TOOLS`.
"""

from __future__ import annotations

import json
from typing import Any

from sqlmodel import Session

from loregarden.mcp.tool_ids import McpTool
from loregarden.mcp.tool_schemas import integer_prop as _integer_prop
from loregarden.mcp.tool_schemas import string_prop as _string_prop
from loregarden.services.devdocs import DEFAULT_LIMIT, MAX_LIMIT, search_reference

TOOL_DEFINITION: dict[str, Any] = {
    "name": McpTool.SEARCH_REFERENCE,
    "description": (
        "Search DevDocs for a documentation page and get back ranked entries "
        "with their URLs. Use this before loregarden_fetch_reference rather "
        "than guessing a URL: pass a returned `url` straight to that tool. "
        "Omit `docset` to get a list of matching docsets to choose from. "
        "Returns a self-classifying payload — read `error_kind` rather than "
        "expecting an exception."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "query": _string_prop("What to look for, e.g. useEffect or Array.prototype.map."),
            "docset": _string_prop(
                "Which docset to search: slug, alias, or name — react, python~3.12, ng. "
                "Omit it to receive suggestions instead of results."
            ),
            "limit": _integer_prop(
                f"How many entries to return. Default {DEFAULT_LIMIT}, capped at {MAX_LIMIT}. "
                "`total_matches` reports the uncapped count."
            ),
        },
        "required": ["query"],
    },
}


def search_reference_tool(session: Session, arguments: dict[str, Any]) -> str:
    """Serialize through the model, as `reference_tool` does and for the reason 607 gives."""
    payload = search_reference(
        session,
        arguments["query"],
        docset=arguments.get("docset", ""),
        limit=arguments.get("limit") or DEFAULT_LIMIT,
    )
    return json.dumps(payload.model_dump(mode="json"), indent=2)
