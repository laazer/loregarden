"""What an args model tells a caller about a field it parses leniently.

Some fields stay text so their parser can accept what an agent writes (a label in
any case, a finding with a part missing) and word its own refusal. Their schema
still has to say what is expected, and it says it from the same source: the enum
class, or the model that describes one item.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from enum import Enum
from typing import Any

from pydantic import BaseModel

JsonSchemaExtra = Callable[[dict[str, Any]], None]


def one_of(choices: type[Enum]) -> JsonSchemaExtra:
    """Advertise the values of `choices` as the field's enum."""
    return one_of_values([member.value for member in choices])


def one_of_values(values: Iterable[str]) -> JsonSchemaExtra:
    """Advertise `values` as the field's enum."""
    listed = list(values)

    def extra(schema: dict[str, Any]) -> None:
        schema["enum"] = listed

    return extra


def items_like(model: type[BaseModel]) -> JsonSchemaExtra:
    """Advertise each item of a list field as `model` describes it."""

    def extra(schema: dict[str, Any]) -> None:
        # Flat: a `$ref` here would name a definition the outer schema lacks.
        # The class docstring is for whoever reads the code; the field says what it is.
        items = flat_schema(model.model_json_schema())
        items.pop("description", None)
        schema["items"] = items

    return extra


def _definitions(node: Any, found: dict[str, Any]) -> None:
    if isinstance(node, dict):  # py-org: allow-isinstance - JSON Schema node
        found.update(node.get("$defs", {}))
        for value in node.values():
            _definitions(value, found)
    elif isinstance(node, list):  # py-org: allow-isinstance - JSON Schema node
        for value in node:
            _definitions(value, found)


#: Keywords whose value is a schema, a list of schemas, or a map of names to
#: schemas. Nothing else is walked: a `default`, an `enum` or a property named
#: `title` is data, not a keyword.
_SUBSCHEMA = frozenset({"items", "additionalProperties", "not"})
_SUBSCHEMA_LISTS = frozenset({"anyOf", "oneOf", "allOf", "prefixItems"})
_SUBSCHEMA_MAPS = frozenset({"properties", "patternProperties"})


def _flat(node: dict[str, Any], definitions: Mapping[str, Any]) -> Any:
    """`node` with every `$ref` inlined and the titles and `$defs` dropped."""
    if "$ref" in node:
        target = definitions[node["$ref"].rsplit("/", 1)[-1]]
        siblings = {key: value for key, value in node.items() if key != "$ref"}
        return _flat({**target, **siblings}, definitions)
    flat: dict[str, Any] = {}
    for key, value in node.items():
        if key in ("title", "$defs"):
            continue
        if key in _SUBSCHEMA_MAPS:
            flat[key] = {name: _flat(sub, definitions) for name, sub in value.items()}
        elif key in _SUBSCHEMA_LISTS:
            flat[key] = [_flat(sub, definitions) for sub in value]
        elif key in _SUBSCHEMA and value not in (True, False):
            flat[key] = _flat(value, definitions)
        elif key == "description":
            # An attribute docstring keeps its line breaks; a schema reads as one line.
            flat[key] = " ".join(value.split())
        else:
            flat[key] = value
    return _without_null(flat)


def _without_null(node: dict[str, Any]) -> dict[str, Any]:
    """`X | None = None` as plain `X`: omitting the field is how to send None."""
    branches = node.get("anyOf")
    if branches is None or "default" not in node or node["default"] is not None:
        return node
    kept = [branch for branch in branches if branch != {"type": "null"}]
    if len(kept) != 1:
        return node
    outer = {key: value for key, value in node.items() if key not in ("anyOf", "default")}
    return {**kept[0], **outer}


def flat_schema(raw: dict[str, Any]) -> dict[str, Any]:
    """`raw` (a model's JSON Schema) with every `$ref` inlined and the titles dropped,
    in the flat shape Claude Code's validator takes."""
    definitions: dict[str, Any] = {}
    _definitions(raw, definitions)
    return _flat(raw, definitions)
