"""JSON Schema builders shared by every module that declares an MCP tool.

They lived in ``mcp.tools`` while that module declared every tool. Once tools
started moving out to their own modules, importing them back from ``mcp.tools``
would have inverted the import graph — ``mcp.tools`` already imports those
modules to dispatch to them — so the builders sit below both.
"""

from __future__ import annotations

from typing import Any


def tool_schema(
    *,
    properties: dict[str, dict[str, Any]],
    required: list[str],
) -> dict[str, Any]:
    """JSON Schema shape compatible with Claude Code / Zod MCP validators."""
    return {
        "type": "object",
        "properties": properties,
        "required": required,
        "additionalProperties": False,
    }


def string_prop(description: str) -> dict[str, str]:
    return {"type": "string", "description": description}


def integer_prop(description: str) -> dict[str, str]:
    return {"type": "integer", "description": description}


def enum_string_prop(description: str, values: list[str]) -> dict[str, Any]:
    return {"type": "string", "description": description, "enum": values}


def boolean_prop(description: str) -> dict[str, str]:
    return {"type": "boolean", "description": description}


def string_list_prop(description: str) -> dict[str, Any]:
    return {"type": "array", "description": description, "items": {"type": "string"}}
