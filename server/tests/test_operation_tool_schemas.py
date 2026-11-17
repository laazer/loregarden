"""An operation tool's schema is its args model's, less what the tool holds back.

The model validates the call on every transport. A schema written beside it
drifted: fields the model read that no agent was told of, enums the model never
checked, and a REST-only knob that only the argument normalizer kept from the
CLI. These pin the schema to the model, and the tool's narrowing to the server.
"""

from __future__ import annotations

import json
from enum import StrEnum
from typing import Any
from unittest import mock

import pytest
from loregarden.mcp.operation_tools import (
    OPERATION_TOOLS,
    TOOL_SHAPES,
    execute_operation_tool,
    operation_tool_schema,
)
from loregarden.mcp.tool_ids import McpTool
from loregarden.mcp.tools import TOOL_DEFINITIONS
from loregarden.services.operations import Caller, Transport
from loregarden.services.operations.advertised import flat_schema, items_like, one_of
from loregarden.services.operations.call import OPERATIONS
from pydantic import BaseModel, ConfigDict, Field

_MCP = Caller(transport=Transport.MCP)
_DECLARED = {str(tool["name"]): tool["inputSchema"] for tool in TOOL_DEFINITIONS}


def _keywords(node: Any, path: str = "") -> list[tuple[str, str]]:
    """Every (path, keyword) in a schema, walking only where schemas nest."""
    found = [(path, key) for key in node]
    for key, sub in node.get("properties", {}).items():
        found += _keywords(sub, f"{path}.{key}")
    if isinstance(node.get("items"), dict):  # py-org: allow-isinstance - JSON Schema node
        found += _keywords(node["items"], f"{path}[]")
    for branch in node.get("anyOf", []):
        found += _keywords(branch, path)
    return found


@pytest.mark.parametrize("tool", sorted(OPERATION_TOOLS))
def test_the_listed_schema_is_the_generated_one(tool: str):
    assert _DECLARED[tool] == operation_tool_schema(tool)


@pytest.mark.parametrize("tool", sorted(OPERATION_TOOLS))
def test_the_schema_is_flat_and_every_field_is_described(tool: str):
    """Claude Code's validator takes no `$ref`, and an agent reads the description."""
    schema = _DECLARED[tool]
    keywords = _keywords(schema)
    assert not [path for path, key in keywords if key in ("$ref", "$defs", "title")]
    undescribed = [
        name for name, prop in schema["properties"].items() if not prop.get("description")
    ]
    assert not undescribed, f"{tool}: describe {undescribed} on the args model"


@pytest.mark.parametrize("tool", sorted(OPERATION_TOOLS))
def test_every_model_field_is_offered_or_held_back_on_purpose(tool: str):
    model = OPERATIONS[OPERATION_TOOLS[tool]].args
    shape = TOOL_SHAPES.get(tool)
    hidden = shape.hidden if shape else frozenset()
    named = (hidden | shape.required | shape.choices.keys()) if shape else frozenset()
    assert named <= model.model_fields.keys(), f"{tool}'s shape names no field of {model.__name__}"
    assert set(_DECLARED[tool]["properties"]) == model.model_fields.keys() - hidden
    if shape:
        assert shape.required <= set(_DECLARED[tool]["required"])


@pytest.mark.parametrize(
    ("tool", "field"),
    [(tool, field) for tool, shape in TOOL_SHAPES.items() for field in shape.choices],
)
def test_a_narrowed_field_offers_values_the_model_takes(tool: str, field: str):
    model = OPERATIONS[OPERATION_TOOLS[tool]].args
    full = flat_schema(model.model_json_schema())["properties"][field]["enum"]
    assert set(TOOL_SHAPES[tool].choices[field]) < set(full)
    assert _DECLARED[tool]["properties"][field]["enum"] == list(TOOL_SHAPES[tool].choices[field])


@pytest.mark.parametrize(
    ("tool", "arguments", "refusal"),
    [
        # The operator's run knobs: the normalizer drops these; the CLI's `mcp call`
        # and any other path into execute_operation_tool now meets the same wall.
        (
            McpTool.START_ORCHESTRATION,
            {"ticket_id": "t", "auto_approve": True},
            "does not take auto_approve",
        ),
        (
            McpTool.SET_INITIATIVE_AUTOPILOT,
            {"initiative_id": "i", "schedule": {}},
            "does not take schedule",
        ),
        (McpTool.REQUEUE_TICKET, {"ticket_id": "t"}, "requires reason"),
        (
            McpTool.REQUEUE_TICKET,
            {"ticket_id": "t", "reason": "r", "state": "done"},
            "state must be one of backlog, in_progress",
        ),
    ],
)
def test_the_server_holds_a_call_to_the_tools_shape(
    tool: McpTool, arguments: dict[str, Any], refusal: str
):
    session = mock.Mock()
    with (
        mock.patch("loregarden.mcp.operation_tools.run_operation") as run,
        pytest.raises(ValueError, match=refusal),
    ):
        execute_operation_tool(session, tool.value, arguments, _MCP)
    run.assert_not_called()


@pytest.mark.parametrize("state", ["backlog", "", None])
def test_a_call_inside_the_shape_runs(state: str | None):
    with mock.patch(
        "loregarden.mcp.operation_tools.run_operation", return_value={"ok": True}
    ) as run:
        reply = execute_operation_tool(
            mock.Mock(),
            McpTool.REQUEUE_TICKET.value,
            {"ticket_id": "t", "reason": "r", "state": state},
            _MCP,
        )
    assert json.loads(reply or "") == {"ok": True}
    run.assert_called_once()


class _Colour(StrEnum):
    RED = "red"
    BLUE = "blue"


class _Item(BaseModel):
    """For whoever reads the code."""

    model_config = ConfigDict(use_attribute_docstrings=True)

    colour: _Colour
    """Which colour."""


class _Args(BaseModel):
    model_config = ConfigDict(use_attribute_docstrings=True)

    title: str
    """A field that happens to share a keyword's name."""
    meta: dict[str, str] = {"title": "kept"}  # noqa: RUF012 - a default to read back
    """Its default is data."""
    picked: _Colour | None = None
    """A wrapped
    description."""
    label: str = Field(default="red", json_schema_extra=one_of(_Colour))
    """Text, advertised as the enum's values."""
    items: list[Any] = Field(default_factory=list, json_schema_extra=items_like(_Item))
    """Items shaped like _Item."""


def test_flattening_reads_keywords_not_names_or_data():
    props = flat_schema(_Args.model_json_schema())["properties"]

    assert props["title"]["description"] == "A field that happens to share a keyword's name."
    assert props["meta"]["default"] == {"title": "kept"}
    assert props["picked"] == {
        "type": "string",
        "enum": ["red", "blue"],
        "description": "A wrapped description.",
    }
    assert props["label"]["enum"] == ["red", "blue"] and props["label"]["type"] == "string"
    assert props["items"]["items"] == {
        "type": "object",
        "properties": {
            "colour": {"type": "string", "enum": ["red", "blue"], "description": "Which colour."}
        },
        "required": ["colour"],
    }
