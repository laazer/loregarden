"""MCP handlers for the operator moves triage could describe but not make.

Baxter could read a ticket and say what was wrong with it, then had to hand the
operator a list of clicks: move this to the right workspace, put it back on a
workflow, give the exhausted stage its budget back, replace it with a ticket
that says what we actually meant. Each of those is an existing service call with
no tool in front of it, which is what made the rail feel advisory even when it
had tools.

Split from ``ticket_edit_tools`` on the seam between editing a ticket's content
and changing where it sits or how it runs: the edits are routine, these carry a
reason and are worth an approval.
"""

from __future__ import annotations

import json
from typing import Any

from sqlmodel import Session, select

from loregarden.mcp.ticket_edit_tools import ticket_state_payload
from loregarden.mcp.tool_ids import McpTool
from loregarden.mcp.tool_schemas import enum_string_prop, string_prop, tool_schema
from loregarden.models.domain import (
    StageStatus,
    Ticket,
    TicketState,
    UpdateTicketRequest,
    Workspace,
)
from loregarden.services.orchestration import OrchestrationService
from loregarden.services.ticket_ids import reissue_in_workspace
from loregarden.services.ticket_relations import TicketRelationService
from loregarden.services.ticket_service import TicketService


def _workspace_by_slug(session: Session, slug: str) -> Workspace:
    workspace = session.exec(select(Workspace).where(Workspace.slug == slug)).first()
    if workspace is None:
        known = ", ".join(
            sorted(ws.slug for ws in session.exec(select(Workspace)).all()) or ["none"]
        )
        raise ValueError(f"Unknown workspace: {slug!r}. Known workspaces: {known}")
    return workspace


def _descendants(session: Session, ticket_id: str) -> list[Ticket]:
    """Every ticket under ``ticket_id``, depth-first.

    A subtree cannot straddle two workspaces — the tree view, the workspace
    filter and `TicketService.create_ticket`'s parent check all assume it does
    not — so a move takes the children with it rather than orphaning them in the
    workspace their parent just left.
    """
    out: list[Ticket] = []
    frontier = [ticket_id]
    while frontier:
        current = frontier.pop()
        children = session.exec(select(Ticket).where(Ticket.parent_ticket_id == current)).all()
        out.extend(children)
        frontier.extend(child.id for child in children)
    return out


def _move_ticket_workspace(session: Session, svc, arguments: dict[str, Any]) -> str:
    """Move a ticket and its subtree to another workspace.

    Refuses a move that would leave the ticket parented in the workspace it is
    leaving: a cross-workspace parent edge is the corruption this tool exists to
    fix, not one it should create. The caller either re-parents in the
    destination or says explicitly to detach.
    """
    ticket = svc.resolve_ticket(ticket_id=arguments["ticket_id"])
    destination = _workspace_by_slug(session, arguments["workspace_slug"])
    if ticket.workspace_id == destination.id:
        raise ValueError(f"Ticket is already in workspace {destination.slug!r}.")

    new_parent = (arguments.get("new_parent_ticket_id") or "").strip()
    detach_parent = bool(arguments.get("detach_parent"))
    if new_parent:
        parent = svc.resolve_ticket(ticket_id=new_parent)
        if parent.workspace_id != destination.id:
            raise ValueError(
                f"New parent {parent.external_id!r} is not in workspace {destination.slug!r}."
            )
        if parent.id == ticket.id:
            raise ValueError("A ticket cannot be its own parent.")
        ticket.parent_ticket_id = parent.id
    elif ticket.parent_ticket_id:
        if not detach_parent:
            raise ValueError(
                "This ticket has a parent in the workspace it is leaving. Pass "
                "new_parent_ticket_id to re-parent it in the destination, or "
                "detach_parent=true to move it to the top level."
            )
        # None, not "": the column references tickets.id, and no ticket has an
        # empty id. Every other detach path in the codebase writes None.
        ticket.parent_ticket_id = None

    moved = [ticket, *_descendants(session, ticket.id)]
    for item in moved:
        item.workspace_id = destination.id
        item.revision += 1
        item.last_updated_by = "triage"
        session.add(item)
    # Ticket numbers are per workspace, so the whole subtree is re-issued here;
    # the ids it arrived under stay resolvable as legacy ids.
    reissue_in_workspace(session, moved, destination)
    session.commit()

    return json.dumps(
        {
            "moved": [{"id": item.id, "external_id": item.external_id} for item in moved],
            "workspace_slug": destination.slug,
            "ticket": ticket_state_payload(session, ticket.id),
        },
        indent=2,
    )


def _set_ticket_workflow(session: Session, svc, arguments: dict[str, Any]) -> str:
    """Put a ticket on a workflow template, or move it to a different stage.

    Both halves go through ``update_ticket_manual`` rather than writing the
    columns here, so a stage reset gets the retry-budget refresh and the state
    reconciliation the panel's own edit gets.
    """
    ticket = svc.resolve_ticket(ticket_id=arguments["ticket_id"])
    fields: dict[str, Any] = {}

    if "workflow_template_slug" in arguments:
        fields["workflow_template_slug"] = arguments["workflow_template_slug"]

    stage_key = (arguments.get("stage_key") or "").strip()
    stage_status = (arguments.get("stage_status") or "").strip()
    if stage_key and not stage_status:
        fields["workflow_stage_key"] = stage_key
    elif stage_key and stage_status:
        fields["stage_key"] = stage_key
        fields["stage_status"] = StageStatus(stage_status)
    elif stage_status:
        raise ValueError("stage_status was given without stage_key.")

    if not fields:
        raise ValueError("Nothing to change — supply workflow_template_slug, stage_key, or both.")

    OrchestrationService(session).update_ticket_manual(ticket, UpdateTicketRequest(**fields))
    return json.dumps(ticket_state_payload(session, ticket.id), indent=2)


def _requeue_ticket(session: Session, svc, arguments: dict[str, Any]) -> str:
    """Clear a block and hand the stage back its dispatch budget.

    The circuit breaker persists its dispatch markers across orchestration runs
    precisely so a stage cannot refresh its own budget by restarting. Only a
    deliberate operator decision clears it, which is what this call records: the
    reason is required and lands on the ticket, so the next reader sees why the
    counter was reset rather than finding it mysteriously empty.
    """
    reason = (arguments.get("reason") or "").strip()
    if not reason:
        raise ValueError("A reason is required — it is the record of why the block was cleared.")

    ticket = svc.resolve_ticket(ticket_id=arguments["ticket_id"])
    stage_key = (arguments.get("stage_key") or ticket.workflow_stage_key or "").strip()
    if not stage_key:
        raise ValueError("This ticket is not on a workflow stage — nothing to requeue.")

    orch = OrchestrationService(session)
    orch.update_ticket_manual(
        ticket,
        UpdateTicketRequest(
            stage_key=stage_key,
            stage_status=StageStatus.PENDING,
            state=TicketState(arguments.get("state") or TicketState.BACKLOG.value),
        ),
    )
    # `refresh_stage_retry_budget` only clears blocking text when this breaker's
    # own structural mark is on the stage; a requeue clears the block whatever
    # wrote it, and says who did it.
    orch.refresh_stage_retry_budget(ticket, stage_key)
    ticket.blocking_issues = ""
    ticket.next_status = ""
    ticket.revision += 1
    ticket.last_updated_by = "triage"
    session.add(ticket)
    session.commit()

    svc.attach_artifact(
        ticket,
        kind="context",
        title=f"Requeued — {stage_key}",
        content={
            "title": f"Requeued — {stage_key}",
            "rows": [
                {"k": "Stage", "v": stage_key},
                {"k": "Reason", "v": reason},
            ],
        },
    )
    return json.dumps(ticket_state_payload(session, ticket.id), indent=2)


def _supersede_ticket(session: Session, svc, arguments: dict[str, Any]) -> str:
    """Replace a ticket with a corrected one and close the original.

    Editing a wrong ticket in place loses the record that it was ever wrong, and
    its runs, artifacts and evidence stay attached to a description they no
    longer match. The replacement is a new ticket in the same place, related to
    the original, and the original is closed `wont_do` naming its successor.
    """
    reason = (arguments.get("reason") or "").strip()
    if not reason:
        raise ValueError("A reason is required — it is what the closed ticket will say.")

    original = svc.resolve_ticket(ticket_id=arguments["ticket_id"])
    workspace = session.get(Workspace, original.workspace_id)
    if workspace is None:
        raise ValueError("Ticket workspace not found.")

    replacement = TicketService(session).create_ticket(
        workspace_slug=workspace.slug,
        title=arguments["title"],
        work_item_type=original.work_item_type,
        parent_ticket_id=original.parent_ticket_id or None,
        description=arguments.get("description", ""),
        acceptance_criteria=arguments.get("acceptance_criteria") or [],
        priority=original.priority,
    )
    TicketRelationService(session).add_relation(original.id, replacement.id, created_by="triage")

    OrchestrationService(session).update_ticket_manual(
        original,
        UpdateTicketRequest(state=TicketState.WONT_DO),
    )
    original.blocking_issues = f"Superseded by {replacement.external_id} — {reason}"
    original.revision += 1
    original.last_updated_by = "triage"
    session.add(original)
    session.commit()

    return json.dumps(
        {
            "superseded": {"id": original.id, "external_id": original.external_id},
            "replacement": {
                "id": replacement.id,
                "external_id": replacement.external_id,
                "title": replacement.title,
            },
            "reason": reason,
        },
        indent=2,
    )


#: Tool declarations, appended to `mcp.tools`' catalog at import.
TICKET_OPS_TOOL_DEFINITIONS: list[dict[str, Any]] = [
    {
        "name": McpTool.MOVE_TICKET_WORKSPACE,
        "description": (
            "Move a ticket, and everything under it, to another workspace. Use when a "
            "ticket was filed against the wrong repo — a platform change raised while "
            "reading a game ticket, say. Refuses to leave the ticket parented in the "
            "workspace it is leaving: pass new_parent_ticket_id or detach_parent."
        ),
        "inputSchema": tool_schema(
            properties={
                "ticket_id": string_prop("Loregarden ticket UUID or external id."),
                "workspace_slug": string_prop("Destination workspace slug."),
                "new_parent_ticket_id": string_prop(
                    "Parent to adopt in the destination workspace (UUID or external_id)."
                ),
                "detach_parent": {
                    "type": "boolean",
                    "description": (
                        "Move the ticket to the top level of the destination instead of "
                        "re-parenting it. Ignored when new_parent_ticket_id is given."
                    ),
                },
            },
            required=["ticket_id", "workspace_slug"],
        ),
    },
    {
        "name": McpTool.SET_TICKET_WORKFLOW,
        "description": (
            "Put a ticket on a workflow template, or move it to a different stage. "
            "Send an empty workflow_template_slug to take it off its workflow entirely. "
            "Resetting a stage to 'pending' also restores that stage's dispatch budget."
        ),
        "inputSchema": tool_schema(
            properties={
                "ticket_id": string_prop("Loregarden ticket UUID or external id."),
                "workflow_template_slug": string_prop(
                    "Workflow template slug to assign. Empty string removes the workflow."
                ),
                "stage_key": string_prop(
                    "Stage to move the ticket to. Alone, it just becomes the current stage."
                ),
                "stage_status": enum_string_prop(
                    "Status to set on stage_key. Requires stage_key.",
                    [status.value for status in StageStatus],
                ),
            },
            required=["ticket_id"],
        ),
    },
    {
        "name": McpTool.REQUEUE_TICKET,
        "description": (
            "Clear a ticket's block and give the stage back its dispatch budget, so it "
            "can run again. The retry budget persists across orchestration runs on "
            "purpose — only a deliberate decision clears it — so a reason is required "
            "and is recorded on the ticket."
        ),
        "inputSchema": tool_schema(
            properties={
                "ticket_id": string_prop("Loregarden ticket UUID or external id."),
                "reason": string_prop(
                    "Why this block is being cleared — what changed since it was raised."
                ),
                "stage_key": string_prop(
                    "Stage to requeue. Defaults to the ticket's current workflow stage."
                ),
                "state": enum_string_prop(
                    "Ticket state to leave it in. Defaults to 'backlog'.",
                    ["backlog", "in_progress"],
                ),
            },
            required=["ticket_id", "reason"],
        ),
    },
    {
        "name": McpTool.SUPERSEDE_TICKET,
        "description": (
            "Replace a ticket with a corrected one: creates a new ticket beside it, "
            "relates the two, and closes the original 'wont_do' naming its successor. "
            "Use when a ticket's premise is wrong rather than incomplete — editing it "
            "in place would leave its runs and evidence attached to a description they "
            "no longer match. For an ordinary correction, use loregarden_update_ticket."
        ),
        "inputSchema": tool_schema(
            properties={
                "ticket_id": string_prop("The ticket to supersede (UUID or external_id)."),
                "title": string_prop("Title of the replacement ticket."),
                "description": string_prop("Description of the replacement ticket."),
                "acceptance_criteria": {
                    "type": "array",
                    "description": "Acceptance criteria for the replacement, one per entry.",
                    "items": {"type": "string"},
                },
                "reason": string_prop("Why the original is being retired."),
            },
            required=["ticket_id", "title", "reason"],
        ),
    },
]


def normalize_ticket_ops_args(
    name: str,
    args: dict[str, Any],
    *,
    coerce_string,
    coerce_optional_string,
    coerce_string_list,
) -> dict[str, Any] | None:
    """Argument whitelists for this module's tools; None for a name it does not own.

    The coercers are injected rather than imported, for the same reason
    `normalize_update_ticket_args` injects them: this module sits below
    ``mcp.tools`` in the import graph and must stay there.
    """
    if name == McpTool.MOVE_TICKET_WORKSPACE:
        payload = {
            "ticket_id": coerce_string(args.get("ticket_id"), field="ticket_id"),
            "workspace_slug": coerce_string(args.get("workspace_slug"), field="workspace_slug"),
            "new_parent_ticket_id": coerce_optional_string(args.get("new_parent_ticket_id")),
            "detach_parent": bool(args.get("detach_parent")),
        }
        return payload

    if name == McpTool.SET_TICKET_WORKFLOW:
        payload = {"ticket_id": coerce_string(args.get("ticket_id"), field="ticket_id")}
        # Presence, not truthiness: "" is the documented way to take a ticket off
        # its workflow, so it must survive normalization.
        if args.get("workflow_template_slug") is not None:
            payload["workflow_template_slug"] = coerce_string(
                args.get("workflow_template_slug"), field="workflow_template_slug"
            )
        for field in ("stage_key", "stage_status"):
            if args.get(field) is not None:
                payload[field] = coerce_string(args.get(field), field=field)
        return payload

    if name == McpTool.REQUEUE_TICKET:
        return {
            "ticket_id": coerce_string(args.get("ticket_id"), field="ticket_id"),
            "reason": coerce_string(args.get("reason"), field="reason"),
            "stage_key": coerce_optional_string(args.get("stage_key")),
            "state": coerce_optional_string(args.get("state")),
        }

    if name == McpTool.SUPERSEDE_TICKET:
        return {
            "ticket_id": coerce_string(args.get("ticket_id"), field="ticket_id"),
            "title": coerce_string(args.get("title"), field="title"),
            "reason": coerce_string(args.get("reason"), field="reason"),
            "description": coerce_optional_string(args.get("description")),
            "acceptance_criteria": coerce_string_list(
                args.get("acceptance_criteria") or [], field="acceptance_criteria"
            ),
        }

    return None


#: Handlers this module owns, keyed by tool name.
_OPS_HANDLERS = {
    "loregarden_move_ticket_workspace": _move_ticket_workspace,
    "loregarden_set_ticket_workflow": _set_ticket_workflow,
    "loregarden_requeue_ticket": _requeue_ticket,
    "loregarden_supersede_ticket": _supersede_ticket,
}


def execute_ticket_ops_tool(
    name: str, session: Session, svc, arguments: dict[str, Any]
) -> str | None:
    """Run ``name`` if this module owns it, else return None so the caller continues."""
    handler = _OPS_HANDLERS.get(name)
    if handler is None:
        return None
    return handler(session, svc, arguments)
