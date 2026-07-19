"""In-process MCP tool implementations — shared by HTTP mount and optional stdio proxy."""

from __future__ import annotations

import json
from typing import Any

from sqlmodel import Session

from loregarden.models.domain import (
    OrchestrationDriver,
    OrchestrationRunStatus,
    TicketState,
    UpdateTicketRequest,
    Workspace,
)
from loregarden.services.builtin_orchestrator import BuiltinOrchestrator
from loregarden.services.evidence import (
    ARTIFACT_KIND as EVIDENCE_ARTIFACT_KIND,
)
from loregarden.services.evidence import (
    EVIDENCE_KINDS,
    resolve_head_sha,
)
from loregarden.services.memory_store import AgentMemoryService
from loregarden.services.orchestration import OrchestrationService
from loregarden.services.orchestration_callbacks import OrchestrationCallbackService
from loregarden.services.orchestration_profile import resolve_orchestration_profile
from loregarden.services.prior_work import search_prior_work
from loregarden.services.ticket_discovery import list_tickets_mcp, ticket_neighbors_mcp


def _tool_schema(
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


def _string_prop(description: str) -> dict[str, str]:
    return {"type": "string", "description": description}


def _integer_prop(description: str) -> dict[str, str]:
    return {"type": "integer", "description": description}


def _enum_string_prop(description: str, values: list[str]) -> dict[str, Any]:
    return {"type": "string", "description": description, "enum": values}


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


def _coerce_optional_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        raise ValueError("max_stages must be an integer")
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        return int(text)
    raise ValueError("max_stages must be an integer")


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
    payload: dict[str, Any] = {}
    if args.get("workspace_slug") is not None:
        payload["workspace_slug"] = _coerce_optional_string(args.get("workspace_slug")) or ""
    return payload


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
        payload = {
            "ticket_id": _coerce_string(args.get("ticket_id"), field="ticket_id"),
        }
        if args.get("state") is not None:
            payload["state"] = _coerce_string(args.get("state"), field="state")
        if not payload.get("state"):
            raise ValueError("state is required")
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

    memory_payload = _normalize_memory_tool_args(name, args)
    if memory_payload is not None:
        return memory_payload

    return args


def _ticket_state_payload(session: Session, ticket_id: str) -> dict[str, Any]:
    svc = OrchestrationCallbackService(session)
    ticket = svc.resolve_ticket(ticket_id=ticket_id)
    active = svc.get_active_orchestration_run(ticket.id)
    orch = OrchestrationService(session)
    return {
        "ticket_id": ticket.id,
        "external_id": ticket.external_id,
        "state": ticket.state.value,
        "workflow_stage_key": ticket.workflow_stage_key,
        "workflow_stage_status": ticket.workflow_stage_status.value,
        "next_agent": ticket.next_agent,
        "blocking_issues": ticket.blocking_issues,
        "active_orchestration": (
            {
                "id": active.id,
                "run_code": active.run_code,
                "status": active.status.value,
                "driver": active.driver.value,
                "current_stage_key": active.current_stage_key,
            }
            if active
            else None
        ),
        "stages": [s.model_dump() for s in orch.build_stage_views(ticket)],
        "hierarchy": ticket_neighbors_mcp(session, ticket),
    }


def _resolve_ticket_payload(
    session: Session,
    *,
    ticket_id: str | None = None,
    external_id: str | None = None,
    workspace_slug: str | None = None,
) -> dict[str, Any]:
    svc = OrchestrationCallbackService(session)
    ticket = svc.resolve_ticket(
        ticket_id=ticket_id,
        external_id=external_id,
        workspace_slug=workspace_slug,
    )
    return _ticket_state_payload(session, ticket.id)


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
        "name": "loregarden_get_ticket",
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
        "name": "loregarden_list_tickets",
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
        "name": "loregarden_get_ticket_by_external",
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
        "name": "loregarden_start_orchestration",
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
        "name": "loregarden_start_stage",
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
        "name": "loregarden_complete_stage",
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
        "name": "loregarden_skip_stage",
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
        "name": "loregarden_block_ticket",
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
        "name": "loregarden_attach_evidence",
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
                    "real surface a user touches, or a verifier's verdict.",
                    list(EVIDENCE_KINDS),
                ),
                "title": _string_prop("Short description of what was captured."),
                "content_json": _string_prop("Captured output as a JSON string."),
            },
            required=["run_id", "evidence_kind", "title"],
        ),
    },
    {
        "name": "loregarden_attach_artifact",
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
        "name": "loregarden_request_approval",
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
        "name": "loregarden_complete_orchestration",
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
        "name": "loregarden_update_ticket",
        "description": "Manually update ticket state (e.g. mark done after implementation).",
        "inputSchema": _tool_schema(
            properties={
                "ticket_id": _string_prop("Loregarden ticket UUID or external_id slug."),
                "state": _enum_string_prop(
                    "New ticket state.",
                    ["backlog", "in_progress", "blocked", "done", "wont_do"],
                ),
            },
            required=["ticket_id", "state"],
        ),
    },
    {
        "name": "loregarden_memory_status",
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
        "name": "loregarden_append_learning",
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
        "name": "loregarden_upsert_memory",
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
        "name": "loregarden_upsert_blog_post",
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
        "name": "loregarden_append_checkpoint",
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
        "name": "loregarden_write_handoff",
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
        "name": "loregarden_search_prior_work",
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
        "name": "loregarden_search_memory",
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
        "name": "loregarden_create_memory_relation",
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


def _start_orchestration(session: Session, svc, arguments: dict[str, Any]) -> str:
    """Start a run on whichever driver the workspace profile selects."""
    ticket = svc.resolve_ticket(ticket_id=arguments["ticket_id"])
    ws = session.get(Workspace, ticket.workspace_id)
    if not ws:
        raise ValueError("Workspace not found")
    profile = resolve_orchestration_profile(ws)
    driver_name = arguments.get("driver") or profile.driver.value
    driver = OrchestrationDriver(driver_name)

    if driver == OrchestrationDriver.BUILTIN_AUTOPILOT:
        run = BuiltinOrchestrator(session).execute(
            ticket, profile, max_stages=arguments.get("max_stages")
        )
    elif driver == OrchestrationDriver.EXTERNAL_MCP:
        run = svc.start_orchestration_run(ticket, driver=driver, profile_slug=profile.slug)
    else:
        raise ValueError(f"Unsupported driver for MCP start: {driver_name}")
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


def execute_tool(session: Session, name: str, arguments: dict[str, Any] | Any) -> str:
    svc = OrchestrationCallbackService(session)
    arguments = normalize_tool_arguments(name, arguments)

    if name == "loregarden_get_ticket":
        return json.dumps(
            _resolve_ticket_payload(
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
            _resolve_ticket_payload(
                session,
                external_id=arguments["external_id"],
                workspace_slug=arguments["workspace_slug"],
            ),
            indent=2,
        )

    if name == "loregarden_start_orchestration":
        return _start_orchestration(session, svc, arguments)

    if name == "loregarden_update_ticket":
        ticket = svc.resolve_ticket(ticket_id=arguments["ticket_id"])
        orch = OrchestrationService(session)
        body = UpdateTicketRequest(state=TicketState(arguments["state"]))
        orch.update_ticket_manual(ticket, body)
        return json.dumps(_ticket_state_payload(session, ticket.id), indent=2)

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

    if name == "loregarden_search_prior_work":
        return json.dumps(
            {
                "results": search_prior_work(
                    session,
                    arguments["query"],
                    workspace_slug=arguments.get("workspace_slug", ""),
                    exclude_ticket_id=arguments.get("ticket_id", ""),
                )
            },
            indent=2,
        )

    memory_result = _execute_memory_tool(name, arguments)
    if memory_result is not None:
        return memory_result

    run_id = arguments.get("run_id")
    if not run_id:
        raise ValueError("run_id is required")

    run = _get_run(session, run_id)
    ticket = svc.resolve_ticket(ticket_id=run.ticket_id)

    if name == "loregarden_start_stage":
        svc.start_stage(
            run,
            ticket,
            stage_key=arguments["stage_key"],
            agent_id=arguments.get("agent_id", ""),
        )
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
