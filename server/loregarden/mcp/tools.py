"""In-process MCP tool implementations — shared by HTTP mount and optional stdio proxy."""

from __future__ import annotations

import json
from typing import Any

from sqlmodel import Session

from loregarden.models.domain import OrchestrationDriver, OrchestrationRunStatus, Workspace
from loregarden.services.builtin_orchestrator import BuiltinOrchestrator
from loregarden.services.orchestration import OrchestrationService
from loregarden.services.orchestration_callbacks import OrchestrationCallbackService
from loregarden.services.orchestration_profile import resolve_orchestration_profile


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


def normalize_tool_arguments(name: str, arguments: Any) -> dict[str, Any]:
    """Coerce Claude MCP bridge quirks (aliases, stringified JSON, camelCase)."""
    args = _coerce_mapping(arguments)

    alias_map = {
        "ticket_id": ("ticketId", "id"),
        "workspace_slug": ("workspaceSlug", "workspace"),
        "external_id": ("externalId", "slug"),
        "run_id": ("runId",),
        "stage_key": ("stageKey", "stage"),
        "agent_id": ("agentId",),
        "skill_name": ("skillName",),
        "content_json": ("contentJson", "content"),
        "next_agent": ("nextAgent",),
    }
    for canonical, aliases in alias_map.items():
        if canonical in args:
            continue
        for alias in aliases:
            if alias in args:
                args[canonical] = args.pop(alias)
                break

    if name == "loregarden_get_ticket":
        return {"ticket_id": _coerce_string(args.get("ticket_id"), field="ticket_id")}

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

    if name in {
        "loregarden_start_stage",
        "loregarden_complete_stage",
        "loregarden_skip_stage",
        "loregarden_request_approval",
    }:
        payload = {
            "run_id": _coerce_string(args.get("run_id"), field="run_id"),
            "stage_key": _coerce_string(args.get("stage_key"), field="stage_key"),
        }
        if name == "loregarden_start_stage":
            payload["agent_id"] = _coerce_optional_string(args.get("agent_id"))
        if name == "loregarden_complete_stage":
            payload["next_agent"] = _coerce_optional_string(args.get("next_agent"))
        if name == "loregarden_skip_stage":
            payload["reason"] = _coerce_optional_string(args.get("reason"))
        if name == "loregarden_request_approval":
            payload["title"] = _coerce_optional_string(args.get("title"))
            payload["impact"] = _coerce_optional_string(args.get("impact"))
        return payload

    if name == "loregarden_block_ticket":
        return {
            "run_id": _coerce_string(args.get("run_id"), field="run_id"),
            "message": _coerce_string(args.get("message"), field="message"),
            "stage_key": _coerce_optional_string(args.get("stage_key")),
        }

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
    }


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
        "description": "Read ticket workflow state, stage map, and active orchestration run.",
        "inputSchema": _tool_schema(
            properties={
                "ticket_id": _string_prop("Loregarden ticket UUID from the run prompt."),
            },
            required=["ticket_id"],
        ),
    },
    {
        "name": "loregarden_get_ticket_by_external",
        "description": "Read ticket state by workspace slug and external_id.",
        "inputSchema": _tool_schema(
            properties={
                "workspace_slug": _string_prop("Workspace slug, e.g. loregarden."),
                "external_id": _string_prop("Ticket external id slug, e.g. 03-wire-cli-agent-runner."),
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
        "description": "Mark a stage done and advance the workflow cursor.",
        "inputSchema": _tool_schema(
            properties={
                "run_id": _string_prop("Orchestration run UUID."),
                "stage_key": _string_prop("Workflow stage key."),
                "next_agent": _string_prop("Optional next agent hint."),
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
]


def _get_run(session: Session, run_id: str):
    from loregarden.models.domain import OrchestrationRun

    run = session.get(OrchestrationRun, run_id)
    if not run:
        raise ValueError(f"Orchestration run not found: {run_id}")
    return run


def execute_tool(session: Session, name: str, arguments: dict[str, Any] | Any) -> str:
    svc = OrchestrationCallbackService(session)
    arguments = normalize_tool_arguments(name, arguments)

    if name == "loregarden_get_ticket":
        return json.dumps(_ticket_state_payload(session, arguments["ticket_id"]), indent=2)

    if name == "loregarden_get_ticket_by_external":
        ticket = svc.resolve_ticket(
            external_id=arguments["external_id"],
            workspace_slug=arguments["workspace_slug"],
        )
        return json.dumps(_ticket_state_payload(session, ticket.id), indent=2)

    if name == "loregarden_start_orchestration":
        ticket = svc.resolve_ticket(ticket_id=arguments["ticket_id"])
        ws = session.get(Workspace, ticket.workspace_id)
        if not ws:
            raise ValueError("Workspace not found")
        profile = resolve_orchestration_profile(ws)
        driver_name = arguments.get("driver") or profile.driver.value
        driver = OrchestrationDriver(driver_name)
        max_stages = arguments.get("max_stages")

        if driver == OrchestrationDriver.BUILTIN_AUTOPILOT:
            run = BuiltinOrchestrator(session).execute(
                ticket,
                profile,
                max_stages=max_stages,
            )
        elif driver == OrchestrationDriver.EXTERNAL_MCP:
            run = svc.start_orchestration_run(
                ticket,
                driver=driver,
                profile_slug=profile.slug,
            )
        else:
            raise ValueError(f"Unsupported driver for MCP start: {driver_name}")
        return json.dumps(_run_view(run), indent=2)

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
