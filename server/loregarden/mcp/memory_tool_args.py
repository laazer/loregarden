"""Argument normalization for the memory, learning, blog-post and checkpoint tools.

Split out of `mcp.tools`, which is at its module-size limit: these seven tools
share a vocabulary (`tags`, `aliases`, `workspace_slug`) that none of the other
tool families use, and `normalize_memory_tool_args` is the only entry point
`normalize_tool_arguments` needs.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from loregarden.mcp.memory_tools import MEMORY_TOOL_NAMES
from loregarden.mcp.tool_args import coerce_optional_bool as _coerce_optional_bool
from loregarden.mcp.tool_args import coerce_optional_int as _coerce_optional_int
from loregarden.mcp.tool_args import coerce_optional_string as _coerce_optional_string
from loregarden.mcp.tool_args import coerce_string as _coerce_string
from loregarden.mcp.tool_args import coerce_tag_list as _coerce_tag_list


def _coerce_tags(args: dict[str, Any], payload: dict[str, Any]) -> None:
    if args.get("tags") is not None:
        payload["tags"] = _coerce_tag_list(args["tags"], field="tags")


def _coerce_aliases(args: dict[str, Any], payload: dict[str, Any]) -> None:
    """Aliases arrive in the same shapes tags do."""
    if args.get("aliases") is not None:
        payload["aliases"] = _coerce_tag_list(args["aliases"], field="aliases")


def _normalize_upsert_memory_args(args: dict[str, Any]) -> dict[str, Any]:
    payload = {
        "title": _coerce_string(args.get("title"), field="title"),
        "workspace_slug": _coerce_string(args.get("workspace_slug"), field="workspace_slug"),
    }
    for field in ("node_id", "body", "ticket_id"):
        if args.get(field) is not None:
            payload[field] = _coerce_optional_string(args.get(field))
    if args.get("discredited") is not None:
        payload["discredited"] = _coerce_optional_bool(args.get("discredited"))
    _coerce_tags(args, payload)
    return payload


def _normalize_append_learning_args(args: dict[str, Any]) -> dict[str, Any]:
    payload = {
        "ticket_id": _coerce_string(args.get("ticket_id"), field="ticket_id"),
        "workspace_slug": _coerce_string(args.get("workspace_slug"), field="workspace_slug"),
        "content": _coerce_string(args.get("content"), field="content"),
        "title": _coerce_optional_string(args.get("title")) or "",
    }
    _coerce_tags(args, payload)
    _coerce_aliases(args, payload)
    return payload


def _normalize_upsert_blog_post_args(args: dict[str, Any]) -> dict[str, Any]:
    payload = {
        "ticket_id": _coerce_string(args.get("ticket_id"), field="ticket_id"),
        "workspace_slug": _coerce_string(args.get("workspace_slug"), field="workspace_slug"),
        "title": _coerce_string(args.get("title"), field="title"),
        "body": _coerce_string(args.get("body"), field="body"),
    }
    if args.get("note_id") is not None:
        payload["note_id"] = _coerce_optional_string(args.get("note_id"))
    _coerce_tags(args, payload)
    return payload


def _normalize_append_checkpoint_args(args: dict[str, Any]) -> dict[str, Any]:
    return {
        "ticket_id": _coerce_string(args.get("ticket_id"), field="ticket_id"),
        "workspace_slug": _coerce_string(args.get("workspace_slug"), field="workspace_slug"),
        "run_id": _coerce_string(args.get("run_id"), field="run_id"),
        "entry": _coerce_string(args.get("entry"), field="entry"),
        "blocker": _coerce_optional_bool(args.get("blocker")),
    }


def _normalize_search_memory_args(args: dict[str, Any]) -> dict[str, Any]:
    payload = {
        "query": _coerce_string(args.get("query"), field="query"),
        "limit": _coerce_optional_int(args.get("limit")) or 20,
    }
    if args.get("workspace_slug") is not None:
        payload["workspace_slug"] = _coerce_optional_string(args.get("workspace_slug")) or ""
    return payload


def _normalize_create_memory_relation_args(args: dict[str, Any]) -> dict[str, Any]:
    return {
        "source_id": _coerce_string(args.get("source_id"), field="source_id"),
        "target_id": _coerce_string(args.get("target_id"), field="target_id"),
        "relation_type": _coerce_optional_string(args.get("relation_type")) or "related",
        "workspace_slug": _coerce_string(args.get("workspace_slug"), field="workspace_slug"),
    }


def _normalize_memory_status_args(args: dict[str, Any]) -> dict[str, Any]:
    status_payload: dict[str, Any] = {}
    if args.get("workspace_slug") is not None:
        status_payload["workspace_slug"] = _coerce_optional_string(args.get("workspace_slug")) or ""
    return status_payload


_NORMALIZERS: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = {
    "loregarden_append_learning": _normalize_append_learning_args,
    "loregarden_upsert_memory": _normalize_upsert_memory_args,
    "loregarden_upsert_blog_post": _normalize_upsert_blog_post_args,
    "loregarden_append_checkpoint": _normalize_append_checkpoint_args,
    "loregarden_search_memory": _normalize_search_memory_args,
    "loregarden_create_memory_relation": _normalize_create_memory_relation_args,
    "loregarden_memory_status": _normalize_memory_status_args,
}


def normalize_memory_tool_args(name: str, args: dict[str, Any]) -> dict[str, Any] | None:
    """Normalize arguments for loregarden's memory/learnings/blog-post/checkpoint
    tools. Returns None if `name` isn't one of these (caller falls through)."""
    if name not in MEMORY_TOOL_NAMES:
        return None
    return _NORMALIZERS[name](args)
