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
import logging
from collections.abc import Callable
from typing import Any

from loregarden.mcp.tool_ids import McpTool
from loregarden.services.ticket_documents import DEFAULT_DOCUMENT_KIND

logger = logging.getLogger(__name__)


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
            # An empty mapping makes the schema report every field as missing,
            # which sends the caller hunting a schema bug that was a typo.
            logger.warning("tool arguments were not valid JSON; treating as empty", exc_info=True)
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


#: Substrings that prove a tool call lost arguments on its way here.
#:
#: A literal closing tag for the parameter it appears inside terminates that
#: parameter early, and every argument after it is swallowed into the text. Six
#: tickets reached the database that way with their acceptance criteria stored as
#: prose and their `acceptance_criteria` field empty — which reads to the next
#: agent as a ticket with no criteria, and the known failure from there is that it
#: invents some.
#:
#: The trap is self-propagating: an agent quoting one of those descriptions in
#: order to report the problem produces another one. That is how the seventh was
#: created — by the ticket filed to describe the first six.
TRUNCATION_MARKERS = ("</description>", "</parameter>", "<parameter name=")


def reject_truncated_call(text: str, *, field: str) -> str:
    """Refuse text carrying the wreckage of a mis-serialized call.

    Loud rather than lenient, and deliberately not "strip the markup": the
    fragment is evidence that arguments were LOST, so the surviving text is not
    trustworthy either. Cleaning it would persist a ticket whose criteria
    silently vanished, which is the defect this exists to stop.

    A legitimate `description` really could want to name one of these tags — this
    docstring's own ticket did. It cannot be sent as literal text regardless,
    because the transport truncates it, so failing here costs nothing that was
    ever going to work and tells the author immediately.
    """
    for marker in TRUNCATION_MARKERS:
        if marker in text:
            raise ValueError(
                f"{field} contains {marker!r}, which means this call was truncated "
                "mid-serialization and later arguments were lost. Re-send it, "
                "describing the tag rather than writing it literally."
            )
    return text


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


def _json_string_list(text: str, *, field: str) -> list[Any]:
    try:
        value = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{field} looks like JSON but is not valid JSON: {text[:80]!r}") from exc
    if not isinstance(value, list):  # py-org: allow-isinstance
        raise ValueError(f"{field} must be a JSON array of strings, got {text[:80]!r}")
    return value


def _clean_items(value: Any, *, field: str) -> list[str]:
    if not isinstance(value, list):  # py-org: allow-isinstance
        raise ValueError(f"{field} must be a list of strings")
    return [str(item).strip() for item in value if str(item).strip()]


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
            value = _json_string_list(text, field=field)
        else:
            value = [line.lstrip("-*").strip() for line in text.splitlines()]
    return _clean_items(value, field=field)


#: A tag never legitimately starts with one of these; text that does was meant
#: as JSON, and splitting it on commas is how `'["routing", "rework"]'` became
#: the tags `'["routing"'` and `'"rework"]'` on the live memory graph.
_JSON_OPENERS = ("[", "{", '"')


def coerce_tag_list(value: Any, *, field: str) -> list[str]:
    """Tags or aliases: a list, a JSON-encoded list, or comma-separated text.

    JSON-looking text must parse as an array or the call is rejected — never
    split on commas, which stores fragments of the JSON as tags.
    """
    if isinstance(value, str):  # py-org: allow-isinstance
        text = value.strip()
        if not text:
            return []
        if text.startswith(_JSON_OPENERS):
            value = _json_string_list(text, field=field)
        else:
            value = text.split(",")
    return _clean_items(value, field=field)


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


def normalize_search_reference(args: dict[str, Any]) -> dict[str, Any]:
    """All three declared fields survive, or the pinning test fails.

    `limit` normalizes to 0 when absent rather than being dropped: the service
    treats 0 as "use the default", so the key is always present and a caller
    reading the normalized arguments sees the full shape.
    """
    return {
        "query": coerce_string(args.get("query"), field="query"),
        "docset": coerce_optional_string(args.get("docset")),
        "limit": coerce_optional_int(args.get("limit"), field="limit") or 0,
    }


def normalize_search_prior_work(args: dict[str, Any]) -> dict[str, Any]:
    return {
        "query": coerce_string(args.get("query"), field="query"),
        "workspace_slug": coerce_optional_string(args.get("workspace_slug")) or "",
        "ticket_id": coerce_optional_string(args.get("ticket_id")) or "",
    }


def _coerce_float(value: Any, *, field: str) -> float:
    """A number from an MCP client, which may send it as a string.

    Zero for absent, which the service reads as "no explicit price given" — and
    which it refuses rather than defaulting, so an unparseable value cannot
    quietly become a small claim.
    """
    if value is None or value == "":
        return 0.0
    try:
        return float(value)
    except (TypeError, ValueError):
        logger.warning("%s was not a number (%r); treating it as unset", field, value)
        return 0.0


def normalize_reserve_docker_capacity(args: dict[str, Any]) -> dict[str, Any]:
    return {
        "holder_label": coerce_string(args.get("holder_label"), field="holder_label"),
        "footprint": coerce_optional_string(args.get("footprint")),
        "cpus": _coerce_float(args.get("cpus"), field="cpus"),
        "memory_mb": coerce_optional_int(args.get("memory_mb"), field="memory_mb") or 0,
        "ttl_seconds": coerce_optional_int(args.get("ttl_seconds"), field="ttl_seconds") or 0,
        "wait_seconds": coerce_optional_int(args.get("wait_seconds"), field="wait_seconds") or 0,
        "ticket_id": coerce_optional_string(args.get("ticket_id")),
        "holder_pid": coerce_optional_int(args.get("holder_pid"), field="holder_pid") or 0,
    }


def normalize_renew_docker_lease(args: dict[str, Any]) -> dict[str, Any]:
    return {
        "lease_id": coerce_string(args.get("lease_id"), field="lease_id"),
        "compose_project": coerce_optional_string(args.get("compose_project")),
        "container_names": coerce_string_list(
            args.get("container_names") or [], field="container_names"
        ),
        "ttl_seconds": coerce_optional_int(args.get("ttl_seconds"), field="ttl_seconds") or 0,
    }


def normalize_release_docker_capacity(args: dict[str, Any]) -> dict[str, Any]:
    return {
        "lease_id": coerce_string(args.get("lease_id"), field="lease_id"),
        "note": coerce_optional_string(args.get("note")),
    }


def normalize_docker_capacity_status(args: dict[str, Any]) -> dict[str, Any]:
    return {
        "lease_id": coerce_optional_string(args.get("lease_id")),
        "include_waiting": (
            True
            if args.get("include_waiting") is None
            else coerce_optional_bool(args.get("include_waiting"))
        ),
    }


def normalize_force_release_docker_lease(args: dict[str, Any]) -> dict[str, Any]:
    return {
        "lease_id": coerce_string(args.get("lease_id"), field="lease_id"),
        "reason": coerce_string(args.get("reason"), field="reason"),
    }


def normalize_list_instances(args: dict[str, Any]) -> dict[str, Any]:
    return {"project": coerce_optional_string(args.get("project"))}


def normalize_launch_instance(args: dict[str, Any]) -> dict[str, Any]:
    params = coerce_mapping(args.get("params"))
    return {
        "template": coerce_string(args.get("template"), field="template"),
        "params": {str(key): str(value) for key, value in params.items()},
        "name": coerce_optional_string(args.get("name")),
        "wait_seconds": coerce_optional_int(args.get("wait_seconds"), field="wait_seconds"),
    }


def normalize_instance_status(args: dict[str, Any]) -> dict[str, Any]:
    return {
        "instance_id": coerce_string(args.get("instance_id"), field="instance_id"),
        "log_lines": coerce_optional_int(args.get("log_lines"), field="log_lines"),
    }


def normalize_stop_instance(args: dict[str, Any]) -> dict[str, Any]:
    return {"instance_id": coerce_string(args.get("instance_id"), field="instance_id")}


#: Cap on one artifact listing. Documents sit on a handful of ancestors; a ticket
#: with hundreds of run artifacts should be narrowed with `kind`, not paged.
MAX_ARTIFACT_LIST_LIMIT = 200


def _default_true(value: Any) -> bool:
    """An omitted flag reads as True; `coerce_optional_bool` would read it as False."""
    return True if value is None or value == "" else coerce_optional_bool(value)


def normalize_write_document(args: dict[str, Any]) -> dict[str, Any]:
    return {
        "ticket_id": coerce_string(args.get("ticket_id"), field="ticket_id"),
        "workspace_slug": coerce_optional_string(args.get("workspace_slug")),
        "title": coerce_string(args.get("title"), field="title"),
        "body": coerce_string(args.get("body"), field="body"),
        "kind": coerce_optional_string(args.get("kind")) or DEFAULT_DOCUMENT_KIND,
        "summary": coerce_optional_string(args.get("summary")),
    }


def normalize_list_artifacts(args: dict[str, Any]) -> dict[str, Any]:
    limit = coerce_optional_int(args.get("limit"), field="limit") or 50
    return {
        "ticket_id": coerce_string(args.get("ticket_id"), field="ticket_id"),
        "workspace_slug": coerce_optional_string(args.get("workspace_slug")),
        "kind": coerce_optional_string(args.get("kind")),
        "include_ancestors": _default_true(args.get("include_ancestors")),
        "latest_only": _default_true(args.get("latest_only")),
        "limit": max(1, min(limit, MAX_ARTIFACT_LIST_LIMIT)),
    }


def normalize_read_artifact(args: dict[str, Any]) -> dict[str, Any]:
    return {"artifact_id": coerce_string(args.get("artifact_id"), field="artifact_id")}


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
    McpTool.SEARCH_REFERENCE.value: normalize_search_reference,
    McpTool.RESERVE_DOCKER_CAPACITY.value: normalize_reserve_docker_capacity,
    McpTool.RENEW_DOCKER_LEASE.value: normalize_renew_docker_lease,
    McpTool.RELEASE_DOCKER_CAPACITY.value: normalize_release_docker_capacity,
    McpTool.DOCKER_CAPACITY_STATUS.value: normalize_docker_capacity_status,
    McpTool.FORCE_RELEASE_DOCKER_LEASE.value: normalize_force_release_docker_lease,
    McpTool.LIST_INSTANCES.value: normalize_list_instances,
    McpTool.LAUNCH_INSTANCE.value: normalize_launch_instance,
    McpTool.INSTANCE_STATUS.value: normalize_instance_status,
    McpTool.STOP_INSTANCE.value: normalize_stop_instance,
    McpTool.WRITE_DOCUMENT.value: normalize_write_document,
    McpTool.LIST_ARTIFACTS.value: normalize_list_artifacts,
    McpTool.READ_ARTIFACT.value: normalize_read_artifact,
}
