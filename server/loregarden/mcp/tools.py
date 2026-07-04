"""In-process MCP tool implementations — shared by HTTP mount and optional stdio proxy."""

from __future__ import annotations

import json
from typing import Any

from sqlmodel import Session

from loregarden.models.domain import OrchestrationDriver, OrchestrationRunStatus
from loregarden.services.builtin_orchestrator import BuiltinOrchestrator
from loregarden.services.orchestration import OrchestrationService
from loregarden.services.orchestration_callbacks import OrchestrationCallbackService
from loregarden.services.orchestration_profile import resolve_orchestration_profile
from loregarden.models.domain import Workspace


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
        "inputSchema": {
            "type": "object",
            "properties": {"ticket_id": {"type": "string"}},
            "required": ["ticket_id"],
        },
    },
    {
        "name": "loregarden_get_ticket_by_external",
        "description": "Read ticket state by workspace slug and external_id.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "workspace_slug": {"type": "string"},
                "external_id": {"type": "string"},
            },
            "required": ["workspace_slug", "external_id"],
        },
    },
    {
        "name": "loregarden_start_orchestration",
        "description": "Start a top-level orchestration run for a ticket.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "ticket_id": {"type": "string"},
                "driver": {
                    "type": "string",
                    "enum": ["builtin_autopilot", "external_mcp"],
                },
                "max_stages": {"type": "integer"},
            },
            "required": ["ticket_id"],
        },
    },
    {
        "name": "loregarden_start_stage",
        "description": "Mark a workflow stage as running before invoking a sub-agent.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "run_id": {"type": "string"},
                "stage_key": {"type": "string"},
                "agent_id": {"type": "string"},
            },
            "required": ["run_id", "stage_key"],
        },
    },
    {
        "name": "loregarden_complete_stage",
        "description": "Mark a stage done and advance the workflow cursor.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "run_id": {"type": "string"},
                "stage_key": {"type": "string"},
                "next_agent": {"type": "string"},
            },
            "required": ["run_id", "stage_key"],
        },
    },
    {
        "name": "loregarden_skip_stage",
        "description": "Mark a stage as won't do.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "run_id": {"type": "string"},
                "stage_key": {"type": "string"},
                "reason": {"type": "string"},
            },
            "required": ["run_id", "stage_key"],
        },
    },
    {
        "name": "loregarden_block_ticket",
        "description": "Block the ticket and fail the orchestration run.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "run_id": {"type": "string"},
                "message": {"type": "string"},
                "stage_key": {"type": "string"},
            },
            "required": ["run_id", "message"],
        },
    },
    {
        "name": "loregarden_attach_artifact",
        "description": "Attach an artifact (log, diff, test output) to a ticket.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "run_id": {"type": "string"},
                "kind": {"type": "string"},
                "title": {"type": "string"},
                "content_json": {"type": "string"},
            },
            "required": ["run_id", "kind", "title"],
        },
    },
    {
        "name": "loregarden_request_approval",
        "description": "Create a human approval inbox item for a stage.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "run_id": {"type": "string"},
                "stage_key": {"type": "string"},
                "title": {"type": "string"},
                "impact": {"type": "string"},
            },
            "required": ["run_id", "stage_key"],
        },
    },
    {
        "name": "loregarden_complete_orchestration",
        "description": "Finish the top-level orchestration run.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "run_id": {"type": "string"},
                "status": {
                    "type": "string",
                    "enum": ["succeeded", "failed", "blocked", "cancelled"],
                },
                "message": {"type": "string"},
            },
            "required": ["run_id"],
        },
    },
]


def _get_run(session: Session, run_id: str):
    from loregarden.models.domain import OrchestrationRun

    run = session.get(OrchestrationRun, run_id)
    if not run:
        raise ValueError(f"Orchestration run not found: {run_id}")
    return run


def execute_tool(session: Session, name: str, arguments: dict[str, Any]) -> str:
    svc = OrchestrationCallbackService(session)

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
