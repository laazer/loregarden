"""Coercing MCP client arguments, and the normalizers dispatched by table.

Split out of ``mcp/tools.py`` at 174, which pushed that module past its size
cap. The seam is real rather than arbitrary: everything here reads a raw
JSON-RPC ``arguments`` object and nothing here knows a tool's behaviour, while
the module it left knows tools and no longer restates how to read an argument.

``isinstance`` is waived throughout. These functions exist to inspect a payload
that arrived over the wire from someone else's client — the "third-party
payload not yet modelled" the organization gate names as its own exception.
Modelling it away is not available here: the whole job is deciding what an
untyped value *is* before anything typed can hold it.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from loregarden.mcp.tool_ids import McpTool


def coerce_mapping(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):  # py-org: allow-isinstance
        return dict(raw)
    if isinstance(raw, str):  # py-org: allow-isinstance
        stripped = raw.strip()
        if not stripped:
            return {}
        try:
            parsed = json.loads(stripped)
        except json.JSONDecodeError:
            return {}
        if isinstance(parsed, dict):  # py-org: allow-isinstance
            return parsed
    return {}


def coerce_string(value: Any, *, field: str) -> str:
    if value is None:
        raise ValueError(f"{field} is required")
    if isinstance(value, str):  # py-org: allow-isinstance
        text = value.strip()
        if not text:
            raise ValueError(f"{field} is required")
        return text
    return str(value).strip()


def coerce_optional_string(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):  # py-org: allow-isinstance
        return value.strip()
    return str(value).strip()


def coerce_optional_int(value: Any, *, field: str = "max_stages") -> int | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):  # py-org: allow-isinstance
        raise ValueError(f"{field} must be an integer")
    if isinstance(value, int):  # py-org: allow-isinstance
        return value
    if isinstance(value, float):  # py-org: allow-isinstance
        if not value.is_integer():
            raise ValueError(f"{field} must be an integer")
        return int(value)
    if isinstance(value, str):  # py-org: allow-isinstance
        text = value.strip()
        if not text:
            return None
        try:
            return int(text)
        except ValueError as exc:
            raise ValueError(f"{field} must be an integer: {exc}") from exc
    raise ValueError(f"{field} must be an integer")


def coerce_string_list(value: Any, *, field: str) -> list[str]:
    """Accept a list, a JSON-encoded list, or newline/bullet text as a list of strings.

    An empty result is preserved rather than treated as absent — clearing a list is
    a legitimate edit, and the caller decides whether the field was sent at all.
    """
    if isinstance(value, str):  # py-org: allow-isinstance
        text = value.strip()
        if not text:
            return []
        if text.startswith("["):
            try:
                value = json.loads(text)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{field} is not valid JSON") from exc
        else:
            value = [line.lstrip("-*").strip() for line in text.splitlines()]
    if not isinstance(value, list):  # py-org: allow-isinstance
        raise ValueError(f"{field} must be a list of strings")
    return [str(item).strip() for item in value if str(item).strip()]


def coerce_optional_bool(value: Any) -> bool:
    if isinstance(value, bool):  # py-org: allow-isinstance
        return value
    if isinstance(value, str):  # py-org: allow-isinstance
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def normalize_fetch_reference(args: dict[str, Any]) -> dict[str, Any]:
    """All three declared fields must survive.

    `test_normalizer_preserves_every_declared_argument` fails otherwise: a field
    the schema advertises and the normalizer drops is a silent no-op — the tool
    accepts the argument and ignores it.
    """
    return {
        "url": coerce_string(args.get("url"), field="url"),
        "refresh": coerce_optional_bool(args.get("refresh")),
        "max_chars": coerce_optional_int(args.get("max_chars"), field="max_chars") or 0,
    }


def normalize_search_prior_work(args: dict[str, Any]) -> dict[str, Any]:
    return {
        "query": coerce_string(args.get("query"), field="query"),
        "workspace_slug": coerce_optional_string(args.get("workspace_slug")) or "",
        "ticket_id": coerce_optional_string(args.get("ticket_id")) or "",
    }


#: Normalizers dispatched by table instead of another branch in the chain below.
#:
#: `execute_tool` got this seam first, as `EXTENDED_TOOLS` — the chain was past
#: the complexity cap and every tool appended to it made the next one harder to
#: add. `normalize_tool_arguments` is the same chain for the same tools and hit
#: the same cap here, so it gets the same answer. A tool that registers a
#: handler in `mcp/tool_registry.py` registers its normalizer here; both
#: residents below are `EXTENDED_TOOLS` residents already.
TABLE_NORMALIZERS: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = {
    McpTool.SEARCH_PRIOR_WORK.value: normalize_search_prior_work,
    McpTool.FETCH_REFERENCE.value: normalize_fetch_reference,
}
