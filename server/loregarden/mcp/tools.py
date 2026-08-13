"""In-process MCP tool implementations — shared by HTTP mount and optional stdio proxy."""

from __future__ import annotations

import json
from typing import Any

from sqlmodel import Session

from loregarden.agents.executors.permission_bridge import is_orchestrated_agent_denied_mcp_tool
from loregarden.mcp.admission import (
    queued_response,
    run_admitted,
    start_orchestration_admitted,
)
from loregarden.mcp.organization_tool import TOOL_DEFINITION as ORGANIZATION_TOOL_DEFINITION
from loregarden.mcp.ticket_edit_tools import (
    execute_ticket_edit_tool,
    normalize_update_ticket_args,
    resolve_ticket_payload,
)
from loregarden.mcp.ticket_ops_tools import (
    TICKET_OPS_TOOL_DEFINITIONS,
    execute_ticket_ops_tool,
    normalize_ticket_ops_args,
)
from loregarden.mcp.tool_ids import McpTool
from loregarden.mcp.tool_registry import EXTENDED_TOOLS
from loregarden.mcp.tool_schemas import enum_string_prop as _enum_string_prop
from loregarden.mcp.tool_schemas import integer_prop as _integer_prop
from loregarden.mcp.tool_schemas import string_prop as _string_prop
from loregarden.mcp.tool_schemas import tool_schema as _tool_schema
from loregarden.models.domain import (
    OrchestrationRunStatus,
    WorkItemType,
)
from loregarden.services.acceptance_criteria import (
    CRITERIA_MODES,
)
from loregarden.services.evidence import (
    ARTIFACT_KIND as EVIDENCE_ARTIFACT_KIND,
)
from loregarden.services.evidence import (
    EVIDENCE_KINDS,
    resolve_head_sha,
)
from loregarden.services.memory_store import AgentMemoryService
from loregarden.services.orchestration_callbacks import OrchestrationCallbackService
from loregarden.services.ticket_discovery import list_tickets_mcp
from loregarden.services.ticket_service import TicketService


def _coerce_mapping(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return dict(raw)
    if isinstance(raw, str):
        stripped = raw.strip()
        if not stripped:
            return {}
        try:
            parsed = json.loads(stripped)
        except json.JSONDecodeError:
            return {}
        if isinstance(parsed, dict):
            return parsed
    return {}


def _coerce_string(value: Any, *, field: str) -> str:
    if value is None:
        raise ValueError(f"{field} is required")
    if isinstance(value, str):
        text = value.strip()
        if not text:
            raise ValueError(f"{field} is required")
        return text
    return str(value).strip()


def _coerce_optional_string(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    return str(value).strip()


def _coerce_optional_int(value: Any, *, field: str = "max_stages") -> int | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        raise ValueError(f"{field} must be an integer")
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if not value.is_integer():
            raise ValueError(f"{field} must be an integer")
        return int(value)
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            return int(text)
        except ValueError as exc:
            raise ValueError(f"{field} must be an integer: {exc}") from exc
    raise ValueError(f"{field} must be an integer")


def _coerce_string_list(value: Any, *, field: str) -> list[str]:
    """Accept a list, a JSON-encoded list, or newline/bullet text as a list of strings.

    An empty result is preserved rather than treated as absent — clearing a list is
    a legitimate edit, and the caller decides whether the field was sent at all.
    """
    if isinstance(value, str):
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
    if not isinstance(value, list):
        raise ValueError(f"{field} must be a list of strings")
    return [str(item).strip() for item in value if str(item).strip()]


def _coerce_optional_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


_MEMORY_TOOL_NAMES = frozenset(
    {
        "loregarden_memory_status",
        "loregarden_append_learning",
        "loregarden_upsert_memory",
        "loregarden_upsert_blog_post",
        "loregarden_append_checkpoint",
        "loregarden_search_memory",
        "loregarden_create_memory_relation",
    }
)


def _coerce_tags(args: dict[str, Any], payload: dict[str, Any]) -> None:
    tags = args.get("tags")
    if tags is None:
        return
    if isinstance(tags, str):
        payload["tags"] = [t.strip() for t in tags.split(",") if t.strip()]
    elif isinstance(tags, list):
        payload["tags"] = [str(t).strip() for t in tags if str(t).strip()]


def _normalize_memory_tool_args(name: str, args: dict[str, Any]) -> dict[str, Any] | None:
    """Normalize arguments for loregarden's memory/learnings/blog-post/checkpoint
    tools. Returns None if `name` isn't one of these (caller falls through)."""
    if name not in _MEMORY_TOOL_NAMES:
        return None

    if name == "loregarden_append_learning":
        payload = {
            "ticket_id": _coerce_string(args.get("ticket_id"), field="ticket_id"),
            "workspace_slug": _coerce_string(args.get("workspace_slug"), field="workspace_slug"),
            "content": _coerce_string(args.get("content"), field="content"),
        }
        _coerce_tags(args, payload)
        return payload

    if name == "loregarden_upsert_memory":
        payload = {
            "title": _coerce_string(args.get("title"), field="title"),
            "workspace_slug": _coerce_string(args.get("workspace_slug"), field="workspace_slug"),
        }
        for field in ("node_id", "body", "ticket_id"):
            if args.get(field) is not None:
                payload[field] = _coerce_optional_string(args.get(field))
        _coerce_tags(args, payload)
        return payload

    if name == "loregarden_upsert_blog_post":
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

    if name == "loregarden_append_checkpoint":
        return {
            "ticket_id": _coerce_string(args.get("ticket_id"), field="ticket_id"),
            "workspace_slug": _coerce_string(args.get("workspace_slug"), field="workspace_slug"),
            "run_id": _coerce_string(args.get("run_id"), field="run_id"),
            "entry": _coerce_string(args.get("entry"), field="entry"),
        }

    if name == "loregarden_search_memory":
        payload = {
            "query": _coerce_string(args.get("query"), field="query"),
            "limit": _coerce_optional_int(args.get("limit")) or 20,
        }
        if args.get("workspace_slug") is not None:
            payload["workspace_slug"] = _coerce_optional_string(args.get("workspace_slug")) or ""
        return payload

    if name == "loregarden_create_memory_relation":
        return {
            "source_id": _coerce_string(args.get("source_id"), field="source_id"),
            "target_id": _coerce_string(args.get("target_id"), field="target_id"),
            "relation_type": _coerce_optional_string(args.get("relation_type")) or "related",
            "workspace_slug": _coerce_string(args.get("workspace_slug"), field="workspace_slug"),
        }

    # loregarden_memory_status
    status_payload: dict[str, Any] = {}
    if args.get("workspace_slug") is not None:
        status_payload["workspace_slug"] = _coerce_optional_string(args.get("workspace_slug")) or ""
    return status_payload


_ALIAS_MAP: dict[str, tuple[str, ...]] = {
    "ticket_id": ("ticketId", "id"),
    "workspace_slug": ("workspaceSlug", "workspace"),
    "external_id": ("externalId", "slug"),
    "run_id": ("runId",),
    "stage_key": ("stageKey", "stage"),
    "agent_id": ("agentId",),
    "skill_name": ("skillName",),
    "content_json": ("contentJson", "content"),
    "next_agent": ("nextAgent",),
    "next_stage_key": ("nextStageKey", "route_to_stage"),
    "blocking_issues": ("blockingIssues",),
    "outcome": ("routeOutcome",),
}


def _declared_properties(name: str) -> frozenset[str]:
    """Argument names `name`'s own schema declares."""
    for tool in TOOL_DEFINITIONS:
        if tool["name"] == name:
            return frozenset(tool.get("inputSchema", {}).get("properties", {}))
    return frozenset()


def _apply_aliases(name: str, args: dict[str, Any]) -> None:
    """Rewrite bridge aliases to canonical names, in place.

    The alias map is global but the tools do not share a vocabulary: one tool's alias is
    another's real argument. `content` is an alias for `content_json` on attach_artifact and
    is also append_learning's own required field, so aliasing it blindly popped `content`
    away and left append_learning reporting it missing on every correct call. Never rewrite
    an argument the target tool declares itself.
    """
    declared = _declared_properties(name)
    for canonical, aliases in _ALIAS_MAP.items():
        if canonical in args:
            continue
        for alias in aliases:
            if alias in args and alias not in declared:
                args[canonical] = args.pop(alias)
                break


_STAGE_SCOPED_TOOLS = frozenset(
    {
        "loregarden_start_stage",
        "loregarden_complete_stage",
        "loregarden_skip_stage",
        "loregarden_request_approval",
    }
)


def _normalize_get_ticket(args: dict[str, Any]) -> dict[str, Any]:
    """Either identifier will do, but one of them must be present."""
    payload: dict[str, Any] = {}
    if args.get("ticket_id") is not None:
        payload["ticket_id"] = _coerce_string(args.get("ticket_id"), field="ticket_id")
    if args.get("external_id") is not None:
        payload["external_id"] = _coerce_string(args.get("external_id"), field="external_id")
    if args.get("workspace_slug") is not None:
        payload["workspace_slug"] = _coerce_string(
            args.get("workspace_slug"), field="workspace_slug"
        )
    if not payload.get("ticket_id") and not payload.get("external_id"):
        raise ValueError("ticket_id or external_id is required")
    return payload


def _normalize_stage_scoped(name: str, args: dict[str, Any]) -> dict[str, Any]:
    """Coerce the run+stage tools, which share a run_id/stage_key core."""
    payload = {
        "run_id": _coerce_string(args.get("run_id"), field="run_id"),
        "stage_key": _coerce_string(args.get("stage_key"), field="stage_key"),
    }
    if name == "loregarden_start_stage":
        payload["agent_id"] = _coerce_optional_string(args.get("agent_id"))
    if name == "loregarden_complete_stage":
        payload["next_agent"] = _coerce_optional_string(args.get("next_agent"))
        payload["next_stage_key"] = _coerce_optional_string(args.get("next_stage_key"))
        payload["outcome"] = _coerce_optional_string(args.get("outcome")) or "pass"
        payload["blocking_issues"] = _coerce_optional_string(args.get("blocking_issues"))
    if name == "loregarden_skip_stage":
        payload["reason"] = _coerce_optional_string(args.get("reason"))
    if name == "loregarden_request_approval":
        payload["title"] = _coerce_optional_string(args.get("title"))
        payload["impact"] = _coerce_optional_string(args.get("impact"))
    return payload


def _normalize_attach_evidence(args: dict[str, Any]) -> dict[str, Any]:
    content_json = args.get("content_json")
    if content_json is not None and not isinstance(content_json, str):
        content_json = json.dumps(content_json)
    return {
        "run_id": _coerce_string(args.get("run_id"), field="run_id"),
        "evidence_kind": _coerce_string(args.get("evidence_kind"), field="evidence_kind"),
        "title": _coerce_string(args.get("title"), field="title"),
        "content_json": _coerce_optional_string(content_json),
    }


def _normalize_ticket_ops_args(name: str, args: dict[str, Any]) -> dict[str, Any] | None:
    """`normalize_ticket_ops_args` with this module's coercers already bound."""
    return normalize_ticket_ops_args(
        name,
        args,
        coerce_string=_coerce_string,
        coerce_optional_string=_coerce_optional_string,
        coerce_string_list=_coerce_string_list,
    )


def normalize_tool_arguments(name: str, arguments: Any) -> dict[str, Any]:
    """Coerce Claude MCP bridge quirks (aliases, stringified JSON, camelCase)."""
    args = _coerce_mapping(arguments)
    _apply_aliases(name, args)

    if name == "loregarden_get_ticket":
        return _normalize_get_ticket(args)

    if name == "loregarden_list_tickets":
        payload = {
            "workspace_slug": _coerce_string(args.get("workspace_slug"), field="workspace_slug"),
        }
        for field in (
            "state",
            "work_item_type",
            "search",
            "parent_ticket_id",
            "parent_external_id",
        ):
            if args.get(field) is not None:
                payload[field] = _coerce_string(args.get(field), field=field)
        if args.get("roots_only") is not None:
            payload["roots_only"] = _coerce_optional_bool(args.get("roots_only"))
        if args.get("limit") is not None:
            payload["limit"] = _coerce_optional_int(args.get("limit")) or 50
        return payload

    if name == "loregarden_get_ticket_by_external":
        return {
            "workspace_slug": _coerce_string(args.get("workspace_slug"), field="workspace_slug"),
            "external_id": _coerce_string(args.get("external_id"), field="external_id"),
        }

    if name == "loregarden_start_orchestration":
        payload = {
            "ticket_id": _coerce_string(args.get("ticket_id"), field="ticket_id"),
        }
        if args.get("driver") is not None:
            payload["driver"] = _coerce_string(args.get("driver"), field="driver")
        max_stages = _coerce_optional_int(args.get("max_stages"))
        if max_stages is not None:
            payload["max_stages"] = max_stages
        return payload

    if name in _STAGE_SCOPED_TOOLS:
        return _normalize_stage_scoped(name, args)

    if name == "loregarden_block_ticket":
        return {
            "run_id": _coerce_string(args.get("run_id"), field="run_id"),
            "message": _coerce_string(args.get("message"), field="message"),
            "stage_key": _coerce_optional_string(args.get("stage_key")),
        }

    if name == "loregarden_search_prior_work":
        return {
            "query": _coerce_string(args.get("query"), field="query"),
            "workspace_slug": _coerce_optional_string(args.get("workspace_slug")) or "",
            "ticket_id": _coerce_optional_string(args.get("ticket_id")) or "",
        }

    if name == "loregarden_attach_evidence":
        return _normalize_attach_evidence(args)

    if name == "loregarden_attach_artifact":
        content_json = args.get("content_json")
        if isinstance(content_json, dict):
            content_json = json.dumps(content_json)
        elif content_json is not None and not isinstance(content_json, str):
            content_json = json.dumps(content_json)
        return {
            "run_id": _coerce_string(args.get("run_id"), field="run_id"),
            "kind": _coerce_string(args.get("kind"), field="kind"),
            "title": _coerce_string(args.get("title"), field="title"),
            "content_json": _coerce_optional_string(content_json),
        }

    if name == "loregarden_complete_orchestration":
        payload = {"run_id": _coerce_string(args.get("run_id"), field="run_id")}
        if args.get("status") is not None:
            payload["status"] = _coerce_string(args.get("status"), field="status")
        payload["message"] = _coerce_optional_string(args.get("message"))
        return payload

    if name == "loregarden_update_ticket":
        return normalize_update_ticket_args(
            args, coerce_string=_coerce_string, coerce_string_list=_coerce_string_list
        )

    if name == "loregarden_create_ticket":
        # Deliberately loose here: title/workspace_slug are passed through
        # (stripped, not rejected) so TicketService.create_ticket owns every
        # validation message — duplicating "Title is required" here with
        # different casing would silently diverge from the service's own text.
        payload = {
            "workspace_slug": _coerce_optional_string(args.get("workspace_slug")),
            "title": _coerce_optional_string(args.get("title")),
            "work_item_type": _coerce_optional_string(args.get("work_item_type")) or "task",
            "description": _coerce_optional_string(args.get("description")),
            "acceptance_criteria": _coerce_string_list(
                args.get("acceptance_criteria") or [], field="acceptance_criteria"
            ),
            "priority": _coerce_optional_int(args.get("priority"), field="priority"),
            "external_id": _coerce_optional_string(args.get("external_id")),
            "parent": _coerce_optional_string(args.get("parent")),
        }
        if payload["priority"] is None:
            payload["priority"] = 3
        return payload

    if name == "loregarden_write_handoff":
        checklist = args.get("checklist")
        if isinstance(checklist, str):
            stripped = checklist.strip()
            if stripped:
                try:
                    checklist = json.loads(stripped)
                except json.JSONDecodeError:
                    pass  # leave as string; the service reports the parse error
        return {
            "ticket_id": _coerce_string(args.get("ticket_id"), field="ticket_id"),
            "workspace_slug": _coerce_string(args.get("workspace_slug"), field="workspace_slug"),
            "from_agent": _coerce_string(
                args.get("from_agent")
                if args.get("from_agent") is not None
                else args.get("fromAgent"),
                field="from_agent",
            ),
            "to_agent": _coerce_string(
                args.get("to_agent") if args.get("to_agent") is not None else args.get("toAgent"),
                field="to_agent",
            ),
            "checklist": checklist,
        }

    # Whatever the branches above did not claim goes to the modules that own
    # their own tools. Each returns None for a name it does not own, and neither
    # ever returns an empty payload — every branch it takes builds a dict with at
    # least the tool's required field — so `or` reads the same as an
    # `is not None` chain here, and `args` remains the fallthrough it always was.
    return _normalize_memory_tool_args(name, args) or _normalize_ticket_ops_args(name, args) or args


def _run_view(run) -> dict[str, Any]:
    return {
        "id": run.id,
        "run_code": run.run_code,
        "ticket_id": run.ticket_id,
        "driver": run.driver.value,
        "profile_slug": run.profile_slug,
        "status": run.status.value,
        "current_stage_key": run.current_stage_key,
        "error_message": run.error_message,
    }


TOOL_DEFINITIONS: list[dict[str, Any]] = [
    {
        "name": McpTool.GET_TICKET,
        "description": "Read ticket workflow state, stage map, hierarchy neighbors, and active orchestration run.",
        "inputSchema": _tool_schema(
            properties={
                "ticket_id": _string_prop(
                    "Loregarden ticket UUID or external_id slug (e.g. 03-wire-cli-agent-runner)."
                ),
                "external_id": _string_prop("Explicit external_id when not using ticket_id."),
                "workspace_slug": _string_prop(
                    "Workspace slug — required when resolving by external_id slug via ticket_id."
                ),
            },
            required=[],
        ),
    },
    {
        "name": McpTool.LIST_TICKETS,
        "description": "Search and list tickets in a workspace (flat results for discovery).",
        "inputSchema": _tool_schema(
            properties={
                "workspace_slug": _string_prop("Workspace slug, e.g. loregarden."),
                "search": _string_prop("Optional title or external_id substring search."),
                "state": _enum_string_prop(
                    "Optional ticket state filter.",
                    ["backlog", "in_progress", "blocked", "done"],
                ),
                "work_item_type": _enum_string_prop(
                    "Optional work item type filter.",
                    ["milestone", "feature", "capability", "task", "bug"],
                ),
                "parent_ticket_id": _string_prop("Optional parent ticket UUID."),
                "parent_external_id": _string_prop("Optional parent external_id slug."),
                "roots_only": {
                    "type": "boolean",
                    "description": "Only top-level tickets (no parent).",
                },
                "limit": _integer_prop("Max results (default 50, max 100)."),
            },
            required=["workspace_slug"],
        ),
    },
    {
        "name": McpTool.GET_TICKET_BY_EXTERNAL,
        "description": "Read ticket state by workspace slug and external_id.",
        "inputSchema": _tool_schema(
            properties={
                "workspace_slug": _string_prop("Workspace slug, e.g. loregarden."),
                "external_id": _string_prop(
                    "Ticket external id slug, e.g. 03-wire-cli-agent-runner."
                ),
            },
            required=["workspace_slug", "external_id"],
        ),
    },
    {
        "name": McpTool.START_ORCHESTRATION,
        "description": "Start a top-level orchestration run for a ticket.",
        "inputSchema": _tool_schema(
            properties={
                "ticket_id": _string_prop("Loregarden ticket UUID."),
                "driver": _enum_string_prop(
                    "Orchestration driver.",
                    ["builtin_autopilot", "external_mcp"],
                ),
                "max_stages": _integer_prop("Optional cap on stages for builtin autopilot."),
            },
            required=["ticket_id"],
        ),
    },
    {
        "name": McpTool.START_STAGE,
        "description": "Mark a workflow stage as running before invoking a sub-agent.",
        "inputSchema": _tool_schema(
            properties={
                "run_id": _string_prop("Orchestration run UUID."),
                "stage_key": _string_prop("Workflow stage key."),
                "agent_id": _string_prop("Optional agent id override."),
            },
            required=["run_id", "stage_key"],
        ),
    },
    {
        "name": McpTool.COMPLETE_STAGE,
        "description": (
            "Mark a stage complete and advance the workflow cursor. "
            "Use outcome=reject with next_stage_key to route back to an upstream agent."
        ),
        "inputSchema": _tool_schema(
            properties={
                "run_id": _string_prop("Orchestration run UUID."),
                "stage_key": _string_prop("Workflow stage key."),
                "next_agent": _string_prop("Optional next agent hint."),
                "next_stage_key": _string_prop(
                    "Optional explicit target stage (for upstream rework)."
                ),
                "outcome": _enum_string_prop("Stage outcome.", ["pass", "reject"]),
                "blocking_issues": _string_prop("Optional rework notes when routing upstream."),
            },
            required=["run_id", "stage_key"],
        ),
    },
    {
        "name": McpTool.SKIP_STAGE,
        "description": "Mark a stage as won't do.",
        "inputSchema": _tool_schema(
            properties={
                "run_id": _string_prop("Orchestration run UUID."),
                "stage_key": _string_prop("Workflow stage key."),
                "reason": _string_prop("Optional skip reason."),
            },
            required=["run_id", "stage_key"],
        ),
    },
    {
        "name": McpTool.BLOCK_TICKET,
        "description": "Block the ticket and fail the orchestration run.",
        "inputSchema": _tool_schema(
            properties={
                "run_id": _string_prop("Orchestration run UUID."),
                "message": _string_prop("Blocking message for operators."),
                "stage_key": _string_prop("Optional stage key context."),
            },
            required=["run_id", "message"],
        ),
    },
    {
        "name": McpTool.ATTACH_EVIDENCE,
        "description": (
            "Attach proof that the work behaves as claimed. The commit it proves is "
            "stamped server-side, so evidence captured before your last edit is "
            "distinguishable from proof of the current code."
        ),
        "inputSchema": _tool_schema(
            properties={
                "run_id": _string_prop("Agent or orchestration run UUID."),
                "evidence_kind": _enum_string_prop(
                    "What this proves: a red-to-green test, output captured from the "
                    "real surface a user touches, a verifier's verdict, or the full "
                    "regression suite passing green at this commit.",
                    list(EVIDENCE_KINDS),
                ),
                "title": _string_prop("Short description of what was captured."),
                "content_json": _string_prop("Captured output as a JSON string."),
            },
            required=["run_id", "evidence_kind", "title"],
        ),
    },
    {
        "name": McpTool.ATTACH_ARTIFACT,
        "description": "Attach an artifact (log, diff, test output) to a ticket.",
        "inputSchema": _tool_schema(
            properties={
                "run_id": _string_prop("Agent or orchestration run UUID."),
                "kind": _string_prop("Artifact kind, e.g. log, diff, test."),
                "title": _string_prop("Short artifact title."),
                "content_json": _string_prop("Optional JSON string payload."),
            },
            required=["run_id", "kind", "title"],
        ),
    },
    {
        "name": McpTool.REQUEST_APPROVAL,
        "description": "Create a human approval inbox item for a stage.",
        "inputSchema": _tool_schema(
            properties={
                "run_id": _string_prop("Orchestration run UUID."),
                "stage_key": _string_prop("Workflow stage key."),
                "title": _string_prop("Approval title."),
                "impact": _string_prop("Impact / description for the operator."),
            },
            required=["run_id", "stage_key"],
        ),
    },
    {
        "name": McpTool.COMPLETE_ORCHESTRATION,
        "description": "Finish the top-level orchestration run.",
        "inputSchema": _tool_schema(
            properties={
                "run_id": _string_prop("Orchestration run UUID."),
                "status": _enum_string_prop(
                    "Final orchestration status.",
                    ["succeeded", "failed", "blocked", "cancelled"],
                ),
                "message": _string_prop("Optional completion message."),
            },
            required=["run_id"],
        ),
    },
    {
        "name": McpTool.UPDATE_TICKET,
        "description": (
            "Update ticket state or content (state, title, description, acceptance "
            "criteria). Supply at least one field besides ticket_id. Acceptance "
            "criteria belong here — never append them to the description."
        ),
        "inputSchema": _tool_schema(
            properties={
                "ticket_id": _string_prop("Loregarden ticket UUID or external_id slug."),
                "state": _enum_string_prop(
                    "New ticket state.",
                    ["backlog", "in_progress", "blocked", "done", "wont_do"],
                ),
                "title": _string_prop("New ticket title."),
                "description": _string_prop("New ticket description (replaces the existing one)."),
                "acceptance_criteria": {
                    "type": "array",
                    "description": (
                        "Acceptance criteria, one per entry. Combined with the stored list "
                        "per 'mode'. Send [] with mode 'replace' to clear them."
                    ),
                    "items": {"type": "string"},
                },
                "mode": _enum_string_prop(
                    "How acceptance_criteria combines with the stored list. 'replace' "
                    "(default) overwrites it; 'append' adds entries not already present. "
                    "Read the ticket first if you mean to replace.",
                    list(CRITERIA_MODES),
                ),
                "tags": {
                    "type": "array",
                    "description": (
                        "Free-form labels. Replaces the stored tags outright — read the "
                        "ticket first and resend the ones to keep. Send [] to clear them."
                    ),
                    "items": {"type": "string"},
                },
            },
            required=["ticket_id"],
        ),
    },
    {
        "name": McpTool.LINK_DEPENDENCY,
        "description": (
            "Link one ticket to wait for another: ticket_id depends on (runs after) "
            "depends_on. Best-effort ordering within a parent's subtree — it does not "
            "hard-block a standalone run. Idempotent; rejects self-links and cycles. "
            "Both ids accept a UUID or external_id slug."
        ),
        "inputSchema": _tool_schema(
            properties={
                "ticket_id": _string_prop("The dependent ticket (UUID or external_id)."),
                "depends_on": _string_prop(
                    "The prerequisite ticket it should wait for (UUID or external_id)."
                ),
            },
            required=["ticket_id", "depends_on"],
        ),
    },
    {
        "name": McpTool.UNLINK_DEPENDENCY,
        "description": (
            "Remove a dependency edge: ticket_id no longer waits for depends_on. "
            "No-op if the edge does not exist. Both ids accept a UUID or external_id."
        ),
        "inputSchema": _tool_schema(
            properties={
                "ticket_id": _string_prop("The dependent ticket (UUID or external_id)."),
                "depends_on": _string_prop("The prerequisite ticket to unlink."),
            },
            required=["ticket_id", "depends_on"],
        ),
    },
    {
        "name": McpTool.LINK_RELATION,
        "description": (
            "Relate two tickets for context: symmetric and non-blocking, so neither "
            "waits for the other and subtree run order is unchanged. Use link_dependency "
            "instead when one must run after the other. Idempotent in both directions; "
            "rejects self-links. Both ids accept a UUID or external_id slug."
        ),
        "inputSchema": _tool_schema(
            properties={
                "ticket_id": _string_prop("One ticket (UUID or external_id)."),
                "related_to": _string_prop("The ticket to relate it to (UUID or external_id)."),
            },
            required=["ticket_id", "related_to"],
        ),
    },
    {
        "name": McpTool.UNLINK_RELATION,
        "description": (
            "Remove a relation between two tickets. No-op if they are not related. "
            "Both ids accept a UUID or external_id slug."
        ),
        "inputSchema": _tool_schema(
            properties={
                "ticket_id": _string_prop("One ticket (UUID or external_id)."),
                "related_to": _string_prop("The ticket to unrelate (UUID or external_id)."),
            },
            required=["ticket_id", "related_to"],
        ),
    },
    {
        "name": McpTool.CREATE_TICKET,
        "description": (
            "Create a new ticket. Mirrors the TicketCreate schema — validation "
            "(including milestone-cannot-have-parent and hierarchy rules) is owned "
            "by TicketService.create_ticket, not reimplemented here. Returns the "
            "created ticket's id, external_id, and title."
        ),
        "inputSchema": _tool_schema(
            properties={
                "workspace_slug": _string_prop("Workspace slug, e.g. loregarden."),
                "title": _string_prop("Ticket title."),
                "work_item_type": _enum_string_prop(
                    "Work item type (default task).",
                    ["milestone", "feature", "capability", "task", "bug"],
                ),
                "description": _string_prop("Ticket description (default empty)."),
                "acceptance_criteria": {
                    "type": "array",
                    "description": "Acceptance criteria, one per entry (default empty).",
                    "items": {"type": "string"},
                },
                "priority": _integer_prop("Priority 1-3 (default 3)."),
                "external_id": _string_prop(
                    "Explicit external_id slug; auto-slugged from the title when empty."
                ),
                "parent": _string_prop(
                    "Parent ticket, as a UUID or external_id slug — resolved the same "
                    "way loregarden_get_ticket resolves ticket_id."
                ),
            },
            required=["workspace_slug", "title"],
        ),
    },
    {
        "name": McpTool.MEMORY_STATUS,
        "description": (
            "Report configured Obsidian/iCloud memory backends and workspace-scoped resolved paths."
        ),
        "inputSchema": _tool_schema(
            properties={
                "workspace_slug": _string_prop(
                    "Workspace slug — returns per-workspace memory, learnings, blog post dirs, and SQLite path."
                ),
            },
            required=[],
        ),
    },
    {
        "name": McpTool.APPEND_LEARNING,
        "description": "Persist ticket learnings to Obsidian notes and/or the memory graph SQLite.",
        "inputSchema": _tool_schema(
            properties={
                "ticket_id": _string_prop("Ticket external id or UUID."),
                "workspace_slug": _string_prop("Workspace slug."),
                "content": _string_prop("Learning body (markdown)."),
                "tags": _string_prop("Optional comma-separated tags or JSON array."),
            },
            required=["ticket_id", "workspace_slug", "content"],
        ),
    },
    {
        "name": McpTool.UPSERT_MEMORY,
        "description": (
            "Upsert a durable memory node under the workspace-scoped Obsidian dir and graph SQLite."
        ),
        "inputSchema": _tool_schema(
            properties={
                "node_id": _string_prop("Optional stable node id for updates."),
                "title": _string_prop("Memory title."),
                "body": _string_prop("Memory body (markdown)."),
                "tags": _string_prop("Optional comma-separated tags or JSON array."),
                "ticket_id": _string_prop("Optional related ticket id."),
                "workspace_slug": _string_prop(
                    "Workspace slug (required — scopes note and graph DB)."
                ),
            },
            required=["title", "workspace_slug"],
        ),
    },
    {
        "name": McpTool.UPSERT_BLOG_POST,
        "description": (
            "Persist a human-readable blog post markdown note under the workspace-scoped BlogPosts Obsidian dir."
        ),
        "inputSchema": _tool_schema(
            properties={
                "ticket_id": _string_prop("Ticket external id or UUID."),
                "workspace_slug": _string_prop("Workspace slug (required — scopes blog post dir)."),
                "title": _string_prop("Blog post title."),
                "body": _string_prop("Blog post body (markdown)."),
                "note_id": _string_prop("Optional stable note id for updates."),
                "tags": _string_prop("Optional comma-separated tags or JSON array."),
            },
            required=["ticket_id", "workspace_slug", "title", "body"],
        ),
    },
    {
        "name": McpTool.APPEND_CHECKPOINT,
        "description": (
            "Append a checkpoint entry (assumption/ambiguity log) for a ticket+run to the "
            "workspace-scoped Checkpoints Obsidian dir — same vault as memory/learnings, "
            "not the workspace repo. Multiple entries accumulate in one ticket+run file."
        ),
        "inputSchema": _tool_schema(
            properties={
                "ticket_id": _string_prop("Ticket external id or UUID."),
                "workspace_slug": _string_prop(
                    "Workspace slug (required — scopes checkpoint dir)."
                ),
                "run_id": _string_prop("Run id for this ticket+run's checkpoint log."),
                "entry": _string_prop(
                    "Checkpoint entry markdown block "
                    "(### [TICKET_ID] Stage — label / Would have asked / Assumption made / Confidence)."
                ),
            },
            required=["ticket_id", "workspace_slug", "run_id", "entry"],
        ),
    },
    {
        "name": McpTool.WRITE_HANDOFF,
        "description": (
            "Write a ticket's project_board/checkpoints/<ticket>/handoff-latest.yaml from a "
            "STRUCTURED checklist, then validate it against the workspace's own handoff gate and "
            "return any violations. Use this instead of hand-writing the YAML: it renders canonical "
            "schema, auto-computes the required/met counters, and on validation FAIL rolls the file "
            "back and returns violations so you can fix and retry in the same turn. Use the exact "
            "item_key/item labels from the frozen catalog for the (from_agent → to_agent) pair "
            "(see mandatory_workflow_gates_v1.md)."
        ),
        "inputSchema": _tool_schema(
            properties={
                "ticket_id": _string_prop("Ticket external id slug or UUID."),
                "workspace_slug": _string_prop("Workspace slug (scopes the repo + gate)."),
                "from_agent": _string_prop("Finishing (upstream) agent, e.g. test_designer."),
                "to_agent": _string_prop("Next (downstream) agent, e.g. test_breaker."),
                "checklist": {
                    "type": "array",
                    "description": (
                        "Checklist items for the pair. Counters are computed for you; do not send "
                        "required_items_met/total_required_items."
                    ),
                    "items": {
                        "type": "object",
                        "properties": {
                            "item_key": _string_prop(
                                "Frozen catalog key, e.g. test_suite_complete."
                            ),
                            "item": _string_prop(
                                "Catalog label text (must match the key's catalog text)."
                            ),
                            "status": _enum_string_prop(
                                "Item status.",
                                ["complete", "incomplete", "deferred", "blocked"],
                            ),
                            "evidence": _string_prop("Evidence (path or attestation text)."),
                            "evidence_type": _string_prop(
                                "Optional 'path' or 'attestation'; defaults to the catalog's type."
                            ),
                            "certainty": _enum_string_prop(
                                "How much this claim is worth, and the only field that "
                                "counts an item as met. 'verified' means an evidence "
                                "artifact on this ticket backs it, and requires "
                                "evidence_artifact_id. 'user_confirmed' means a human "
                                "approved it. 'inferred' means you believe it and have no "
                                "artifact — the default, and honest. Prose in `evidence` "
                                "is not proof; if you ran something, attach it with "
                                "loregarden_attach_evidence and claim verified.",
                                ["verified", "user_confirmed", "inferred"],
                            ),
                            "evidence_artifact_id": _string_prop(
                                "Id of an evidence artifact on this ticket, from "
                                "loregarden_attach_evidence. Required when "
                                "certainty=verified. Evidence captured before your last "
                                "edit reads as stale and stops counting."
                            ),
                            "required": {
                                "type": "boolean",
                                "description": "Optional; defaults true. Match the catalog default.",
                            },
                        },
                        "required": ["item_key", "item", "status"],
                        "additionalProperties": False,
                    },
                },
            },
            required=["ticket_id", "workspace_slug", "from_agent", "to_agent", "checklist"],
        ),
    },
    {
        "name": McpTool.SEARCH_PRIOR_WORK,
        "description": (
            "Find finished tickets like this one and what they hit on the way. Use "
            "before starting work to avoid repeating an approach that already failed."
        ),
        "inputSchema": _tool_schema(
            properties={
                "query": _string_prop("What you are about to work on."),
                "workspace_slug": _string_prop("Workspace slug to search within."),
                "ticket_id": _string_prop("Optional current ticket, excluded from results."),
            },
            required=["query"],
        ),
    },
    {
        "name": McpTool.SEARCH_MEMORY,
        "description": "Search Obsidian notes and memory graph nodes, optionally scoped to a workspace.",
        "inputSchema": _tool_schema(
            properties={
                "query": _string_prop("Search text."),
                "workspace_slug": _string_prop("Optional workspace slug to scope results."),
                "limit": _integer_prop("Max results per backend (default 20)."),
            },
            required=["query"],
        ),
    },
    {
        "name": McpTool.CREATE_MEMORY_RELATION,
        "description": "Link two memory graph nodes in the workspace-scoped SQLite memory store.",
        "inputSchema": _tool_schema(
            properties={
                "source_id": _string_prop("Source memory node id."),
                "target_id": _string_prop("Target memory node id."),
                "relation_type": _string_prop("Relation label (default related)."),
                "workspace_slug": _string_prop("Workspace slug for the memory graph DB."),
            },
            required=["source_id", "target_id", "workspace_slug"],
        ),
    },
]

# Tools that live in their own module rather than in this file's chain.
TOOL_DEFINITIONS.append(ORGANIZATION_TOOL_DEFINITION)
TOOL_DEFINITIONS.extend(TICKET_OPS_TOOL_DEFINITIONS)


def _get_run(session: Session, run_id: str):
    from loregarden.models.domain import OrchestrationRun

    run = session.get(OrchestrationRun, run_id)
    if not run:
        raise ValueError(f"Orchestration run not found: {run_id}")
    return run


def _execute_memory_tool(name: str, arguments: dict[str, Any]) -> str | None:
    """Dispatch loregarden's memory/learnings/blog-post/checkpoint tools.
    Returns None if `name` isn't one of these (caller falls through)."""
    if name not in _MEMORY_TOOL_NAMES:
        return None

    memory = AgentMemoryService.from_settings()

    if name == "loregarden_memory_status":
        return json.dumps(
            memory.status(workspace_slug=arguments.get("workspace_slug", "")), indent=2
        )

    if name == "loregarden_append_learning":
        result = memory.append_learning(
            ticket_id=arguments["ticket_id"],
            workspace_slug=arguments["workspace_slug"],
            content=arguments["content"],
            tags=arguments.get("tags"),
        )
        return json.dumps(result, indent=2)

    if name == "loregarden_upsert_memory":
        result = memory.upsert_memory(
            node_id=arguments.get("node_id", ""),
            title=arguments["title"],
            body=arguments.get("body", ""),
            tags=arguments.get("tags"),
            ticket_id=arguments.get("ticket_id", ""),
            workspace_slug=arguments["workspace_slug"],
        )
        return json.dumps(result, indent=2)

    if name == "loregarden_upsert_blog_post":
        result = memory.upsert_blog_post(
            ticket_id=arguments["ticket_id"],
            workspace_slug=arguments["workspace_slug"],
            title=arguments["title"],
            body=arguments["body"],
            tags=arguments.get("tags"),
            note_id=arguments.get("note_id", ""),
        )
        return json.dumps(result, indent=2)

    if name == "loregarden_append_checkpoint":
        result = memory.append_checkpoint(
            ticket_id=arguments["ticket_id"],
            workspace_slug=arguments["workspace_slug"],
            run_id=arguments["run_id"],
            entry=arguments["entry"],
        )
        return json.dumps(result, indent=2)

    if name == "loregarden_search_memory":
        result = memory.search(
            arguments["query"],
            workspace_slug=arguments.get("workspace_slug", ""),
            limit=int(arguments.get("limit") or 20),
        )
        return json.dumps(result, indent=2)

    # loregarden_create_memory_relation
    result = memory.create_relation(
        source_id=arguments["source_id"],
        target_id=arguments["target_id"],
        relation_type=arguments.get("relation_type", "related"),
        workspace_slug=arguments["workspace_slug"],
    )
    return json.dumps(result, indent=2)


def _create_ticket(
    session: Session, svc: OrchestrationCallbackService, arguments: dict[str, Any]
) -> str:
    """`parent` is resolved via `svc.resolve_ticket` — the same UUID/external_id
    resolution `loregarden_get_ticket` uses — rather than a second lookup, so the two
    never disagree. Deliberately not workspace-scoped here: TicketService.create_ticket
    already rejects a parent from another workspace ("Parent work item not found in
    workspace"), so scoping twice would only risk the two checks disagreeing."""
    work_item_type_raw = arguments.get("work_item_type") or "task"
    try:
        work_item_type = WorkItemType(work_item_type_raw)
    except ValueError as exc:
        raise ValueError(f"Unknown work_item_type: {work_item_type_raw}") from exc

    parent = (arguments.get("parent") or "").strip()
    parent_ticket_id = None
    if parent:
        try:
            parent_ticket_id = svc.resolve_ticket(ticket_id=parent).id
        except ValueError as exc:
            raise ValueError(f"Parent ticket not found: {parent}") from exc

    ticket = TicketService(session).create_ticket(
        workspace_slug=arguments["workspace_slug"],
        title=arguments["title"],
        work_item_type=work_item_type,
        parent_ticket_id=parent_ticket_id,
        description=arguments.get("description", ""),
        acceptance_criteria=arguments.get("acceptance_criteria") or [],
        priority=arguments.get("priority", 3),
        external_id=arguments.get("external_id", ""),
    )
    return json.dumps(
        {"id": ticket.id, "external_id": ticket.external_id, "title": ticket.title}, indent=2
    )


def _start_orchestration(session: Session, svc, arguments: dict[str, Any]) -> str:
    """Start a run on whichever driver the workspace profile selects."""
    reservation, run = start_orchestration_admitted(session, svc, arguments)
    if not reservation.admitted:
        return queued_response(reservation, ticket_id=arguments["ticket_id"])
    return json.dumps(_run_view(run), indent=2)


def _attach_evidence(session: Session, svc, ticket, arguments: dict[str, Any]) -> str:
    """Record proof of behaviour, stamped with the commit it proves."""
    evidence_kind = arguments["evidence_kind"]
    if evidence_kind not in EVIDENCE_KINDS:
        raise ValueError(
            f"Unknown evidence_kind '{evidence_kind}'. Valid kinds: {', '.join(EVIDENCE_KINDS)}"
        )
    content = {}
    if arguments.get("content_json"):
        content = json.loads(arguments["content_json"])
    # Stamped here, not taken from the agent: an agent choosing its own sha can
    # claim proof against a commit its work predates.
    artifact = svc.attach_artifact(
        ticket,
        kind=EVIDENCE_ARTIFACT_KIND,
        title=arguments.get("title", ""),
        content=content,
        evidence_kind=evidence_kind,
        commit_sha=resolve_head_sha(session, ticket),
    )
    return json.dumps(
        {"ok": True, "artifact_id": artifact.id, "commit_sha": artifact.commit_sha}, indent=2
    )


def execute_tool(
    session: Session,
    name: str,
    arguments: dict[str, Any] | Any,
    *,
    orchestrated: bool = False,
) -> str:
    """Dispatch a tool call.

    `orchestrated=True` marks a call made by an agent CLI subprocess Loregarden itself
    supervises (any run built via `agents.cli_adapters.resolve_cli_invocation` — builtin
    autopilot or a manually-started single stage alike; see `ORCHESTRATED_DENIED_MCP_TOOLS`).
    It is threaded down from the MCP transport: the HTTP endpoint sets it from the
    `X-Loregarden-Orchestrated` header, which only Loregarden's own CLI invocation
    builders attach to a run's `--mcp-config`/stdio env. A caller that never sets that
    header — a human's own terminal `claude` session, Ticket Studio chat, a direct
    operator `curl` against `/mcp`, or an `external_mcp`-driven orchestrator calling
    tools/call directly — is NOT covered by this check; `orchestrated` defaults to False
    for exactly that reason. That gap is recorded debt (a9-create-ticket-mcp-tool),
    pending a2-per-agent-server-policy's real per-agent x per-server policy table.
    """
    if orchestrated and is_orchestrated_agent_denied_mcp_tool(name):
        raise ValueError(
            f"{name} is not available to orchestrated pipeline agents (interim policy, "
            "a9-create-ticket-mcp-tool). Use the REST API or an interactive session instead."
        )

    svc = OrchestrationCallbackService(session)
    arguments = normalize_tool_arguments(name, arguments)

    table_handler = EXTENDED_TOOLS.get(name)
    if table_handler is not None:
        return table_handler(session, arguments)

    if name == "loregarden_get_ticket":
        return json.dumps(
            resolve_ticket_payload(
                session,
                ticket_id=arguments.get("ticket_id"),
                external_id=arguments.get("external_id"),
                workspace_slug=arguments.get("workspace_slug"),
            ),
            indent=2,
        )

    if name == "loregarden_list_tickets":
        return json.dumps(
            list_tickets_mcp(
                session,
                workspace_slug=arguments["workspace_slug"],
                state=arguments.get("state"),
                work_item_type=arguments.get("work_item_type"),
                search=arguments.get("search"),
                parent_ticket_id=arguments.get("parent_ticket_id"),
                parent_external_id=arguments.get("parent_external_id"),
                roots_only=bool(arguments.get("roots_only")),
                limit=int(arguments.get("limit") or 50),
            ),
            indent=2,
        )

    if name == "loregarden_get_ticket_by_external":
        return json.dumps(
            resolve_ticket_payload(
                session,
                external_id=arguments["external_id"],
                workspace_slug=arguments["workspace_slug"],
            ),
            indent=2,
        )

    if name == "loregarden_start_orchestration":
        return _start_orchestration(session, svc, arguments)

    # The modules that own their own ticket tools, tried in one branch rather
    # than one branch each: this chain is already well past the complexity cap,
    # so a per-module `if` would tax the next tool anyone adds. Each returns None
    # for a name it does not own, and a handler that runs always returns a
    # non-empty JSON document, so `or` reads the same as an `is not None` chain.
    delegated = execute_ticket_edit_tool(name, session, svc, arguments) or (
        execute_ticket_ops_tool(name, session, svc, arguments)
    )
    if delegated is not None:
        return delegated

    if name == "loregarden_create_ticket":
        return _create_ticket(session, svc, arguments)

    if name == "loregarden_write_handoff":
        from loregarden.services.handoff_writer import write_handoff

        result = write_handoff(
            session,
            ticket_id=arguments["ticket_id"],
            workspace_slug=arguments["workspace_slug"],
            from_agent=arguments["from_agent"],
            to_agent=arguments["to_agent"],
            checklist=arguments["checklist"],
        )
        return json.dumps(result, indent=2)

    memory_result = _execute_memory_tool(name, arguments)
    if memory_result is not None:
        return memory_result

    run_id = arguments.get("run_id")
    if not run_id:
        raise ValueError("run_id is required")

    run = _get_run(session, run_id)
    ticket = svc.resolve_ticket(ticket_id=run.ticket_id)

    if name == "loregarden_start_stage":
        reservation, _ = run_admitted(
            session,
            ticket,
            stage_key=arguments["stage_key"],
            start=lambda: svc.start_stage(
                run,
                ticket,
                stage_key=arguments["stage_key"],
                agent_id=arguments.get("agent_id", ""),
            ),
        )
        if not reservation.admitted:
            return queued_response(reservation, stage_key=arguments["stage_key"])
        reservation.bind(run_id=run.id)
        return json.dumps({"ok": True, "stage_key": arguments["stage_key"]}, indent=2)

    if name == "loregarden_complete_stage":
        svc.complete_stage(
            run,
            ticket,
            stage_key=arguments["stage_key"],
            next_agent=arguments.get("next_agent", ""),
            next_stage_key=arguments.get("next_stage_key", ""),
            outcome=arguments.get("outcome", "pass"),
            blocking_issues=arguments.get("blocking_issues", ""),
        )
        session.refresh(ticket)
        return json.dumps(
            {
                "ok": True,
                "workflow_stage_key": ticket.workflow_stage_key,
                "ticket_state": ticket.state.value,
            },
            indent=2,
        )

    if name == "loregarden_skip_stage":
        svc.skip_stage(
            run,
            ticket,
            stage_key=arguments["stage_key"],
            reason=arguments.get("reason", ""),
        )
        return json.dumps({"ok": True, "stage_key": arguments["stage_key"]}, indent=2)

    if name == "loregarden_block_ticket":
        svc.block_ticket(
            run,
            ticket,
            stage_key=arguments.get("stage_key", ""),
            message=arguments["message"],
        )
        return json.dumps({"ok": True, "ticket_state": ticket.state.value}, indent=2)

    if name == "loregarden_attach_artifact":
        content = {}
        if arguments.get("content_json"):
            content = json.loads(arguments["content_json"])
        artifact = svc.attach_artifact(
            ticket,
            kind=arguments.get("kind", "log"),
            title=arguments.get("title", ""),
            content=content,
        )
        return json.dumps({"ok": True, "artifact_id": artifact.id}, indent=2)

    if name == "loregarden_attach_evidence":
        return _attach_evidence(session, svc, ticket, arguments)

    if name == "loregarden_request_approval":
        approval = svc.request_approval(
            ticket,
            stage_key=arguments["stage_key"],
            title=arguments.get("title", ""),
            impact=arguments.get("impact", ""),
        )
        return json.dumps({"ok": True, "approval_id": approval.id}, indent=2)

    if name == "loregarden_complete_orchestration":
        status = OrchestrationRunStatus(arguments.get("status", "succeeded"))
        run = svc.complete_orchestration(
            run,
            ticket,
            status=status,
            message=arguments.get("message", ""),
        )
        return json.dumps(_run_view(run), indent=2)

    raise ValueError(f"Unknown tool: {name}")


def tool_names() -> list[str]:
    return [t["name"] for t in TOOL_DEFINITIONS]
