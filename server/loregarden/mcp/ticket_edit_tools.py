"""MCP handlers that read or edit one ticket: its state payload, its content,
and the edges between it and other tickets.

Split out of ``mcp.tools`` — that module owns the tool catalog and the dispatch
spine, and every ticket-shaped handler landing there was the reason it kept
growing. The dispatcher below returns ``None`` for a name it does not own, so
``execute_tool`` delegates in one branch instead of one per tool.
"""

from __future__ import annotations

import json
from typing import Any

from sqlmodel import Session

from loregarden.models.domain import Ticket, TicketState, UpdateTicketRequest
from loregarden.services.acceptance_criteria import (
    CRITERIA_MODES,
    load_criteria,
    merge_criteria,
)
from loregarden.services.orchestration import OrchestrationService
from loregarden.services.orchestration_callbacks import OrchestrationCallbackService
from loregarden.services.ticket_dependencies import (
    DependencyCycleError,
    TicketDependencyService,
)
from loregarden.services.ticket_discovery import ticket_neighbors_mcp
from loregarden.services.ticket_relations import TicketRelationService
from loregarden.services.ticket_tags import load_tags, normalize_tags


def ticket_state_payload(session: Session, ticket_id: str) -> dict[str, Any]:
    """Everything an agent needs about one ticket after a write, as plain JSON."""
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
        "tags": load_tags(ticket.tags_json),
        "hierarchy": ticket_neighbors_mcp(session, ticket),
        "depends_on": ticket_summaries(
            session, TicketDependencyService(session).prerequisites(ticket.id)
        ),
        "dependents": ticket_summaries(
            session, TicketDependencyService(session).dependents(ticket.id)
        ),
        "related": ticket_summaries(session, TicketRelationService(session).related(ticket.id)),
    }


def ticket_summaries(session: Session, ticket_ids: list[str]) -> list[dict[str, str]]:
    """The far end of an edge, in the shape both dependencies and relations use."""
    out: list[dict[str, str]] = []
    for tid in ticket_ids:
        dep = session.get(Ticket, tid)
        if dep is not None:
            out.append(
                {
                    "id": dep.id,
                    "external_id": dep.external_id,
                    "title": dep.title,
                    "state": dep.state.value,
                }
            )
    return out


def ticket_body(ticket: Ticket) -> dict[str, Any]:
    """What the ticket *asks for*, as against where it sits in the pipeline.

    Empty is a real answer here. A ticket with no description returns ``""`` and
    no criteria returns ``[]`` rather than omitting the keys: an agent that can
    tell "nobody wrote a requirement" from "I cannot see the requirement" will
    ask instead of inventing one, and inventing one is the failure this exists
    to stop — the invention lands in a test docstring at test-design and every
    later stage treats it as the spec.
    """
    return {
        "title": ticket.title,
        "description": ticket.description,
        "acceptance_criteria": load_criteria(ticket.acceptance_criteria_json),
    }


def resolve_ticket_payload(
    session: Session,
    *,
    ticket_id: str | None = None,
    external_id: str | None = None,
    workspace_slug: str | None = None,
) -> dict[str, Any]:
    """A ticket's workflow position *and* its requirement.

    The body rides on the two read tools rather than on
    ``ticket_state_payload``, which every write tool also returns. Those are two
    different questions asked at two different moments: a `complete_stage` reply
    is asking "where am I now", and answering it with a thousand-word
    description would put the whole requirement back into context on every
    write. A read is where "what am I meant to do" is actually being asked.

    Widening these two is preferred over a separate `get_ticket_body` tool
    because the failure being fixed is an agent not knowing the body exists at
    all — a second tool it must first think to call would reproduce that, and a
    `fields=` parameter has the same defect with more surface.
    """
    svc = OrchestrationCallbackService(session)
    ticket = svc.resolve_ticket(
        ticket_id=ticket_id,
        external_id=external_id,
        workspace_slug=workspace_slug,
    )
    return ticket_state_payload(session, ticket.id) | ticket_body(ticket)


def _collect_update_fields(ticket: Ticket, arguments: dict[str, Any]) -> dict[str, Any]:
    """Turn the tool's arguments into UpdateTicketRequest fields.

    Split from the handler so the "at least one field" guard stays readable as
    the set of editable fields grows.
    """
    fields: dict[str, Any] = {}
    if "state" in arguments:
        fields["state"] = TicketState(arguments["state"])
    if "title" in arguments:
        fields["title"] = arguments["title"]
    if "description" in arguments:
        fields["description"] = arguments["description"]

    mode = arguments.get("mode", "replace")
    if mode not in CRITERIA_MODES:
        raise ValueError(f"mode must be one of {', '.join(CRITERIA_MODES)} (got {mode!r})")

    if "acceptance_criteria" in arguments:
        fields["acceptance_criteria"] = merge_criteria(
            load_criteria(ticket.acceptance_criteria_json),
            arguments["acceptance_criteria"],
            mode,
        )
    elif "mode" in arguments:
        raise ValueError("mode was given without acceptance_criteria")

    if "tags" in arguments:
        fields["tags"] = normalize_tags(arguments["tags"])

    return fields


def update_ticket(session: Session, svc, arguments: dict[str, Any]) -> str:
    """Apply an agent's edits to ticket state and content.

    Refuses a call carrying only ticket_id. Reporting success for a request that
    changed nothing is the failure this tool was widened to fix.
    """
    ticket = svc.resolve_ticket(ticket_id=arguments["ticket_id"])
    fields = _collect_update_fields(ticket, arguments)
    if not fields:
        raise ValueError(
            "Nothing to update — supply at least one of: state, title, "
            "description, acceptance_criteria, tags."
        )

    OrchestrationService(session).update_ticket_manual(ticket, UpdateTicketRequest(**fields))
    return json.dumps(ticket_state_payload(session, ticket.id), indent=2)


def _link_dependency(session: Session, svc, arguments: dict[str, Any]) -> str:
    """Make ``ticket_id`` wait for ``depends_on``. Both accept a UUID or external_id."""
    ticket = svc.resolve_ticket(ticket_id=arguments["ticket_id"])
    prerequisite = svc.resolve_ticket(ticket_id=arguments["depends_on"])
    try:
        TicketDependencyService(session).add_dependency(
            ticket.id, prerequisite.id, created_by="agent"
        )
    except DependencyCycleError as exc:
        raise ValueError(str(exc)) from exc
    return json.dumps(ticket_state_payload(session, ticket.id), indent=2)


def _unlink_dependency(session: Session, svc, arguments: dict[str, Any]) -> str:
    ticket = svc.resolve_ticket(ticket_id=arguments["ticket_id"])
    prerequisite = svc.resolve_ticket(ticket_id=arguments["depends_on"])
    TicketDependencyService(session).remove_dependency(ticket.id, prerequisite.id)
    return json.dumps(ticket_state_payload(session, ticket.id), indent=2)


def _link_relation(session: Session, svc, arguments: dict[str, Any]) -> str:
    """Relate ``ticket_id`` and ``related_to``. Both accept a UUID or external_id."""
    ticket = svc.resolve_ticket(ticket_id=arguments["ticket_id"])
    other = svc.resolve_ticket(ticket_id=arguments["related_to"])
    TicketRelationService(session).add_relation(ticket.id, other.id, created_by="agent")
    return json.dumps(ticket_state_payload(session, ticket.id), indent=2)


def _unlink_relation(session: Session, svc, arguments: dict[str, Any]) -> str:
    ticket = svc.resolve_ticket(ticket_id=arguments["ticket_id"])
    other = svc.resolve_ticket(ticket_id=arguments["related_to"])
    TicketRelationService(session).remove_relation(ticket.id, other.id)
    return json.dumps(ticket_state_payload(session, ticket.id), indent=2)


#: Handlers this module owns, keyed by tool name.
_EDGE_HANDLERS = {
    "loregarden_update_ticket": update_ticket,
    "loregarden_link_dependency": _link_dependency,
    "loregarden_unlink_dependency": _unlink_dependency,
    "loregarden_link_relation": _link_relation,
    "loregarden_unlink_relation": _unlink_relation,
}


def execute_ticket_edit_tool(
    name: str, session: Session, svc, arguments: dict[str, Any]
) -> str | None:
    """Run ``name`` if this module owns it, else return None so the caller continues."""
    handler = _EDGE_HANDLERS.get(name)
    if handler is None:
        return None
    return handler(session, svc, arguments)


def normalize_update_ticket_args(
    args: dict[str, Any], *, coerce_string, coerce_string_list
) -> dict:
    """Whitelist for loregarden_update_ticket.

    Every field the tool accepts must be listed here too — an omission drops the
    argument before the handler sees it, which is exactly how acceptance_criteria
    went missing on the HTTP side. The coercers are injected rather than imported
    to keep this module below ``mcp.tools`` in the import graph.
    """
    payload: dict[str, Any] = {
        "ticket_id": coerce_string(args.get("ticket_id"), field="ticket_id"),
    }
    for field in ("state", "title", "description", "mode"):
        if args.get(field) is not None:
            payload[field] = coerce_string(args.get(field), field=field)
    for field in ("acceptance_criteria", "tags"):
        if args.get(field) is not None:
            payload[field] = coerce_string_list(args.get(field), field=field)
    return payload
