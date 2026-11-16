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

from loregarden.mcp.tool_args import reject_truncated_call
from loregarden.models.domain import (
    Ticket,
    TicketState,
    UpdateTicketRequest,
)
from loregarden.services.acceptance_criteria import (
    CRITERIA_MODES,
    load_criteria,
    merge_criteria,
)
from loregarden.services.orchestration import OrchestrationService
from loregarden.services.ticket_dependencies import (
    DependencyCycleError,
    TicketDependencyService,
)
from loregarden.services.ticket_relations import TicketRelationService
from loregarden.services.ticket_revision_guard import guard_ticket_revision
from loregarden.services.ticket_state_payload import ticket_state_payload
from loregarden.services.ticket_tags import normalize_tags


def _collect_criteria_fields(ticket: Ticket, arguments: dict[str, Any]) -> dict[str, Any]:
    """The acceptance-criteria half of an update: the merge and its `mode`.

    One concern rather than three branches inside the field sweep — `mode` is
    meaningless without the criteria it combines, and saying so here keeps both
    halves of that rule in one place. Returns {} when the call names neither.
    """
    mode = arguments.get("mode", "replace")
    if mode not in CRITERIA_MODES:
        raise ValueError(f"mode must be one of {', '.join(CRITERIA_MODES)} (got {mode!r})")

    if "acceptance_criteria" not in arguments:
        if "mode" in arguments:
            raise ValueError("mode was given without acceptance_criteria")
        return {}

    return {
        "acceptance_criteria": merge_criteria(
            load_criteria(ticket.acceptance_criteria_json),
            arguments["acceptance_criteria"],
            mode,
        )
    }


def _collect_update_fields(ticket: Ticket, arguments: dict[str, Any]) -> dict[str, Any]:
    """Turn the tool's arguments into UpdateTicketRequest fields.

    Split from the handler so the "at least one field" guard stays readable as
    the set of editable fields grows.
    """
    fields: dict[str, Any] = {}
    if "state" in arguments:
        fields["state"] = TicketState(arguments["state"])
    # The only way to clear `state_locked` from MCP. Without it the flag was
    # write-only here: naming a state set it, and nothing in the tool surface
    # could release it, so a pinned ticket could be reached but never freed.
    if "auto_state" in arguments:
        fields["auto_state"] = arguments["auto_state"]
    if "title" in arguments:
        fields["title"] = arguments["title"]
    if "description" in arguments:
        fields["description"] = arguments["description"]

    fields.update(_collect_criteria_fields(ticket, arguments))

    if "tags" in arguments:
        fields["tags"] = normalize_tags(arguments["tags"])

    if "out_of_scope" in arguments:
        fields["out_of_scope"] = arguments["out_of_scope"]

    if "priority" in arguments:
        priority = arguments["priority"]
        if priority < 1 or priority > 3:
            raise ValueError(f"Invalid priority {priority}: must be in [1, 3]")
        fields["priority"] = priority

    return fields


def update_ticket(session: Session, svc, arguments: dict[str, Any]) -> str:
    """Apply an agent's edits to ticket state and content.

    Refuses a call carrying only ticket_id. Reporting success for a request that
    changed nothing is the failure this tool was widened to fix.
    """
    ticket = svc.resolve_ticket(ticket_id=arguments["ticket_id"])
    fields = _collect_update_fields(ticket, arguments)
    if "parent" in arguments:
        # Resolved here rather than in _collect_update_fields because only the
        # handler holds the resolver, and a parent may be named by external id.
        # "" is a deliberate detach (legal only for a milestone), so an empty
        # string must reach reparent_ticket rather than being dropped as falsy.
        parent = (arguments["parent"] or "").strip()
        fields["parent_ticket_id"] = svc.resolve_ticket(ticket_id=parent).id if parent else ""
    if not fields:
        raise ValueError(
            "Nothing to update — supply at least one of: state, auto_state, title, "
            "description, priority, acceptance_criteria, out_of_scope, tags, parent."
        )

    # Read straight off the arguments: the token is an edge concern and must not
    # reach `_collect_update_fields` or `UpdateTicketRequest`, or a call carrying
    # only {ticket_id, expected_revision} would stop being a no-op and become a
    # legal write that burns a revision and changes nothing. Checked after the
    # fields guard for the same reason — a refused call must not claim the row.
    # `is not None`, not truthiness: 0 is a real revision.
    expected_revision = arguments.get("expected_revision")
    if expected_revision is not None:
        guard_ticket_revision(session, ticket_id=ticket.id, expected_revision=expected_revision)

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
    args: dict[str, Any], *, coerce_string, coerce_string_list, coerce_int, coerce_bool
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
    for field in ("state", "title", "description", "mode", "parent"):
        if args.get(field) is not None:
            payload[field] = coerce_string(args.get(field), field=field)
    if "description" in payload:
        # Checked after coercion so the message names the text that will be
        # stored, not the raw argument.
        reject_truncated_call(payload["description"], field="description")
    for field in ("acceptance_criteria", "out_of_scope", "tags"):
        if args.get(field) is not None:
            payload[field] = coerce_string_list(args.get(field), field=field)
    if args.get("priority") is not None:
        payload["priority"] = coerce_int(args.get("priority"), field="priority")
    if args.get("expected_revision") is not None:
        # `is not None`, not truthiness: 0 is the revision every freshly created
        # ticket holds, so a falsy check here would drop the token and leave the
        # write unguarded after the caller asked for a guard.
        payload["expected_revision"] = coerce_int(
            args.get("expected_revision"), field="expected_revision"
        )
    if args.get("auto_state") is not None:
        payload["auto_state"] = coerce_bool(args.get("auto_state"))
    return payload
