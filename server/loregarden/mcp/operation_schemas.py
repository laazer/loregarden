"""An operation tool's input schema, generated from its operation's args model.

The model validates the call on every transport, so it is the one place a field,
its type, its default and its description are written. A tool advertises the
model less what `ToolShape` holds back from agents, in the flat shape Claude
Code's validator takes: no `$ref`, no titles, and an optional field as its own
type rather than `anyOf` with null.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel

from loregarden.services.operations.advertised import flat_schema


@dataclass(frozen=True)
class ToolShape:
    """Where a tool offers agents less than its operation's model accepts.

    The model serves the page too, which may set what an agent may not; the
    tool's schema leaves those out and `execute_operation_tool` refuses them,
    so a caller that skips the schema (the CLI) gets no more than one that reads it.
    """

    #: Model fields this tool does not offer.
    hidden: frozenset[str] = frozenset()
    #: Fields the tool requires that the model leaves optional.
    required: frozenset[str] = frozenset()
    #: A field's allowed values, narrower than the model's.
    choices: Mapping[str, tuple[str, ...]] = field(default_factory=dict)


def input_schema(model: type[BaseModel], shape: ToolShape) -> dict[str, Any]:
    """The JSON Schema a tool bound to `model` advertises."""
    raw = model.model_json_schema()
    properties: dict[str, Any] = {}
    for name, prop in flat_schema(raw).get("properties", {}).items():
        if name in shape.hidden:
            continue
        flat = prop
        if name in shape.choices:
            flat = {**flat, "enum": list(shape.choices[name])}
        properties[name] = flat
    required = [
        name for name in properties if name in shape.required or name in raw.get("required", ())
    ]
    return {
        "type": "object",
        "properties": properties,
        "required": required,
        "additionalProperties": False,
    }
