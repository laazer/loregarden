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
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from sqlmodel import Session, select

from loregarden.mcp.tool_args import coerce_optional_int
from loregarden.mcp.tool_ids import McpTool
from loregarden.mcp.tool_schemas import string_prop, tool_schema
from loregarden.models.domain import (
    Ticket,
    TicketState,
    UpdateTicketRequest,
    WorkItemType,
    Workspace,
)
from loregarden.services.land_ticket import LandSkip, land_ticket
from loregarden.services.landing import parked_on_failed_landing
from loregarden.services.orchestration import OrchestrationService
from loregarden.services.stage_agent_pin import pin_stage_agent
from loregarden.services.ticket_dependencies import refuse_reparent_onto_a_prerequisite
from loregarden.services.ticket_ids import reissue_in_workspace
from loregarden.services.ticket_relations import TicketRelationService
from loregarden.services.ticket_service import TicketService
from loregarden.services.ticket_state_payload import ticket_state_payload
from loregarden.services.ticket_workspace_binding import workspace_for_ticket
from loregarden.services.tree_publish_scheduler import publish_and_settle


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
    if ticket.work_item_type == WorkItemType.INITIATIVE:
        raise ValueError(
            "Cannot move an initiative between workspaces — initiatives have no workspace binding."
        )
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
        refuse_reparent_onto_a_prerequisite(session, ticket.id, parent.id)
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


def _pin_stage_agent(session: Session, svc, arguments: dict[str, Any]) -> str:
    """Steer the next dispatch of a stage to a named agent, once.

    The pin outranks classify scoring and is consumed at dispatch, so it steers
    exactly one re-run — the shape of "the implementer said re-run me as the
    backend implementer" that classify kept re-scoring back to frontend.
    """
    ticket = svc.resolve_ticket(ticket_id=arguments["ticket_id"])
    stage_key = pin_stage_agent(
        session,
        OrchestrationService(session),
        ticket,
        agent_id=arguments["agent_id"],
        reason=(arguments.get("reason") or "").strip(),
        stage_key=(arguments.get("stage_key") or "").strip(),
        actor="triage",
    )
    payload = ticket_state_payload(session, ticket.id)
    payload["pinned"] = {"stage_key": stage_key, "agent_id": ticket.scope_reroute_agent}
    return json.dumps(payload, indent=2)


def _land_ticket(session: Session, svc, arguments: dict[str, Any]) -> str:
    """Land a ticket's work on its target now, outside the workflow.

    For a ticket that finished before landing ran at the terminal stage (717
    was the first), or one closed by hand. Idempotent: an already-landed
    branch merges nothing and records the first commit on the target that
    contained it. A root publishes its work.
    """
    ticket = svc.resolve_ticket(ticket_id=arguments["ticket_id"])
    workspace = workspace_for_ticket(session, ticket)
    if workspace is None:
        raise ValueError(f"Ticket {ticket.external_id} has no workspace to land in")
    parked = parked_on_failed_landing(session, ticket)
    result = land_ticket(session, ticket, workspace)
    payload: dict[str, Any] = {
        "ok": result.ok,
        "branch": result.branch,
        "target": result.target,
        "landed_sha": result.landed_sha,
        "skipped": result.skipped.value if result.skipped else "",
        "conflicted_files": list(result.conflicted_files),
        "detail": result.detail,
    }
    if result.ok and parked:
        # The landing was all that stood between this ticket and done. Finish
        # through the workflow's own path, which re-lands (a no-op now) and
        # derives done, rather than leaving a landed ticket blocked (928).
        svc.orch.finalize_workflow(ticket)
        payload["ticket_state"] = ticket.state.value
    if result.ok and result.skipped in (LandSkip.NO_BRANCH, LandSkip.BASE_TARGET):
        outcome = publish_and_settle(session, ticket, workspace)
        payload["published"] = {
            "ok": outcome.ok,
            "branch": outcome.branch,
            "tip": outcome.tip,
            "pr_url": outcome.pr_url,
            "skipped": outcome.skipped,
            "detail": outcome.detail,
        }
    return json.dumps(payload, indent=2)


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
    workspace = workspace_for_ticket(session, original)
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
    },
    {
        "name": McpTool.REQUEUE_TICKET,
        "description": (
            "Clear a ticket's block and give the stage back its dispatch budget, so it "
            "can run again. The retry budget persists across orchestration runs on "
            "purpose — only a deliberate decision clears it — so a reason is required "
            "and is recorded on the ticket."
        ),
    },
    {
        "name": McpTool.PIN_STAGE_AGENT,
        "description": (
            "Steer the next dispatch of a stage to a named agent, once. Outranks the "
            "classify stage's keyword scoring and is consumed at dispatch. For a "
            "ticket the classifier keeps routing to the wrong specialist — an "
            "implementer that reports 're-run me as backend_implementer' and is "
            "re-scored to frontend again. A reason is required and recorded."
        ),
        "inputSchema": tool_schema(
            properties={
                "ticket_id": string_prop("Loregarden ticket UUID or external id."),
                "agent_id": string_prop(
                    "Agent to dispatch next, e.g. backend_implementer. Must be one the "
                    "stage can run: a classify route, or the stage's static agent."
                ),
                "reason": string_prop("Why routing is being overridden — recorded on the ticket."),
                "stage_key": string_prop(
                    "Stage to pin. Defaults to the ticket's current workflow stage."
                ),
            },
            required=["ticket_id", "agent_id", "reason"],
        ),
    },
    {
        "name": McpTool.LAND_TICKET,
        "description": (
            "Merge a ticket's branch into its target branch now — the integration "
            "branch of its tree, or nothing for a top-level ticket, whose leg is the "
            "publish chain. Idempotent. For a ticket that finished before landing "
            "ran at its terminal stage, or one closed by hand. A tree's root "
            "publishes its integration branch instead."
        ),
        "inputSchema": tool_schema(
            properties={
                "ticket_id": string_prop("Loregarden ticket UUID or external id."),
            },
            required=["ticket_id"],
        ),
    },
    {
        "name": McpTool.MERGE_PULL_REQUEST,
        "description": (
            "Squash-merge a ticket's pull request and clean up after it: removes the "
            "branch's clean worktrees, the local branch and the GitHub branch, reporting "
            "each step. Refused unless GitHub says the PR can merge now (required checks "
            "green, signed commits, no conflicts, reviews satisfied); refused too if the "
            "PR's head is no longer head_sha. Irreversible."
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


@dataclass(frozen=True)
class _Coercers:
    """The coercers `mcp.tools` injects (see `normalize_ticket_ops_args`)."""

    string: Callable[..., str]
    optional_string: Callable[..., str | None]
    string_list: Callable[..., list[str]]


def _move_args(args: dict[str, Any], c: _Coercers) -> dict[str, Any]:
    return {
        "ticket_id": c.string(args.get("ticket_id"), field="ticket_id"),
        "workspace_slug": c.string(args.get("workspace_slug"), field="workspace_slug"),
        "new_parent_ticket_id": c.optional_string(args.get("new_parent_ticket_id")),
        "detach_parent": bool(args.get("detach_parent")),
    }


def _set_workflow_args(args: dict[str, Any], c: _Coercers) -> dict[str, Any]:
    payload = {"ticket_id": c.string(args.get("ticket_id"), field="ticket_id")}
    # Presence, not truthiness: "" is the documented way to take a ticket off
    # its workflow, so it must survive normalization.
    if args.get("workflow_template_slug") is not None:
        payload["workflow_template_slug"] = c.string(
            args.get("workflow_template_slug"), field="workflow_template_slug"
        )
    for field in ("stage_key", "stage_status"):
        if args.get(field) is not None:
            payload[field] = c.string(args.get(field), field=field)
    return payload


def _requeue_args(args: dict[str, Any], c: _Coercers) -> dict[str, Any]:
    return {
        "ticket_id": c.string(args.get("ticket_id"), field="ticket_id"),
        "reason": c.string(args.get("reason"), field="reason"),
        "stage_key": c.optional_string(args.get("stage_key")),
        "state": c.optional_string(args.get("state")),
    }


def _pin_args(args: dict[str, Any], c: _Coercers) -> dict[str, Any]:
    return {
        "ticket_id": c.string(args.get("ticket_id"), field="ticket_id"),
        "agent_id": c.string(args.get("agent_id"), field="agent_id"),
        "reason": c.string(args.get("reason"), field="reason"),
        "stage_key": c.optional_string(args.get("stage_key")),
    }


def _ticket_only_args(args: dict[str, Any], c: _Coercers) -> dict[str, Any]:
    return {"ticket_id": c.string(args.get("ticket_id"), field="ticket_id")}


def _merge_args(args: dict[str, Any], c: _Coercers) -> dict[str, Any]:
    number = coerce_optional_int(args.get("number"), field="number")
    if number is None:
        raise ValueError("number is required")
    return {
        "ticket_id": c.string(args.get("ticket_id"), field="ticket_id"),
        "number": number,
        "head_sha": c.optional_string(args.get("head_sha")),
    }


def _supersede_args(args: dict[str, Any], c: _Coercers) -> dict[str, Any]:
    return {
        "ticket_id": c.string(args.get("ticket_id"), field="ticket_id"),
        "title": c.string(args.get("title"), field="title"),
        "reason": c.string(args.get("reason"), field="reason"),
        "description": c.optional_string(args.get("description")),
        "acceptance_criteria": c.string_list(
            args.get("acceptance_criteria") or [], field="acceptance_criteria"
        ),
    }


#: One argument whitelist per tool. A table rather than an `if` chain: the
#: chain was at McCabe 11 and each new tool added one.
_ARG_NORMALIZERS: dict[McpTool, Callable[[dict[str, Any], _Coercers], dict[str, Any]]] = {
    McpTool.MOVE_TICKET_WORKSPACE: _move_args,
    McpTool.SET_TICKET_WORKFLOW: _set_workflow_args,
    McpTool.REQUEUE_TICKET: _requeue_args,
    McpTool.PIN_STAGE_AGENT: _pin_args,
    McpTool.LAND_TICKET: _ticket_only_args,
    McpTool.MERGE_PULL_REQUEST: _merge_args,
    McpTool.SUPERSEDE_TICKET: _supersede_args,
}


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
    tool = McpTool.try_parse(name)
    normalizer = _ARG_NORMALIZERS.get(tool) if tool else None
    if normalizer is None:
        return None
    return normalizer(args, _Coercers(coerce_string, coerce_optional_string, coerce_string_list))


#: Handlers this module owns, keyed by tool name.
_OPS_HANDLERS = {
    "loregarden_move_ticket_workspace": _move_ticket_workspace,
    "loregarden_pin_stage_agent": _pin_stage_agent,
    "loregarden_land_ticket": _land_ticket,
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
