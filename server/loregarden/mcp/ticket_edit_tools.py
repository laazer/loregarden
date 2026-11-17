"""MCP's argument whitelist for `loregarden_update_ticket`.

The edit itself is an operation (`services/operations/ticket_writes.py`); what
stays here is the coercion of an agent's loosely typed arguments into the
operation's fields, which only MCP and the CLI need.
"""

from __future__ import annotations

from typing import Any

from loregarden.mcp.tool_args import reject_truncated_call


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
