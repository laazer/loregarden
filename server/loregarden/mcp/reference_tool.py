"""The `loregarden_fetch_reference` MCP tool: schema and handler.

Its own module, like `organization_tool`, because `mcp/tools.py` is at its size
cap and `execute_tool` is past its complexity cap — `mcp/tool_registry.py` says
so in its own docstring. A tool that needs only a session and its arguments
registers a handler there and lives here.
"""

from __future__ import annotations

import json
from typing import Any

from sqlmodel import Session

from loregarden.mcp.tool_ids import McpTool
from loregarden.mcp.tool_schemas import integer_prop as _integer_prop
from loregarden.mcp.tool_schemas import string_prop as _string_prop
from loregarden.services.reference_cache import fetch_reference

TOOL_DEFINITION: dict[str, Any] = {
    "name": McpTool.FETCH_REFERENCE,
    "description": (
        "Fetch a documentation page through loregarden's cache, extracted to "
        "markdown. Prefer this over WebFetch for framework and library "
        "documentation: the raw HTML is fetched and extracted once per URL and "
        "every later read is served from the cache, so a run that consults the "
        "same page twice pays for it once. Returns a self-classifying payload — "
        "check `error` rather than expecting an exception, and `cache` to tell a "
        "fresh copy from a served-stale one."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "url": _string_prop("Absolute http(s) URL of the page to fetch."),
            "refresh": {
                "type": "boolean",
                "description": (
                    "Re-fetch even if a fresh copy is cached. Use only with a "
                    "reason to believe the page changed; it discards the saving."
                ),
            },
            "max_chars": _integer_prop(
                "Truncate the returned markdown to this many characters. "
                "0 or absent means no cap. Truncation never affects what is "
                "stored, so a later call can ask for more."
            ),
        },
        "required": ["url"],
    },
}


def fetch_reference_tool(session: Session, arguments: dict[str, Any]) -> str:
    """Serialize through the model rather than around it.

    `fetch_reference` returns a `ReferencePayload` whose `fetched_at` is a
    `datetime`, so `json.dumps` over the payload itself raises — which is the
    defect 607 existed to remove, and the reason this calls `model_dump` first.
    """
    payload = fetch_reference(
        session,
        arguments["url"],
        refresh=arguments.get("refresh", False),
        max_chars=arguments.get("max_chars") or 0,
    )
    return json.dumps(payload.model_dump(mode="json"), indent=2)
