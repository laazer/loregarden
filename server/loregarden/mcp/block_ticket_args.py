"""Argument coercion for `loregarden_block_ticket`, prepared action included.

Split from `mcp.tools`, which sits on its 1500-line cap. The coercers are
injected, keeping this module below `mcp.tools` in the import graph the way
`ticket_edit_tools` already is. Turning the arguments into a `PreparedAction`
is `services.prepared_action.prepared_action_from`.
"""

from __future__ import annotations

from typing import Any


def normalize_block_ticket(
    args: dict[str, Any], *, coerce_string, coerce_optional_string, coerce_string_list
) -> dict[str, Any]:
    """Whitelist for loregarden_block_ticket, including the prepared action.

    The whitelist drops anything unlisted, so every prepared-action field has to
    be named here or it reaches the handler as None and the block silently loses
    what the agent prepared (lg-workflow-integrity-460).
    """
    payload: dict[str, Any] = {
        "run_id": coerce_string(args.get("run_id"), field="run_id"),
        "message": coerce_string(args.get("message"), field="message"),
        "stage_key": coerce_optional_string(args.get("stage_key")),
    }
    for field in ("tier", "attempted", "prepared", "command", "script_path"):
        if args.get(field) is not None:
            payload[field] = coerce_optional_string(args.get(field))
    if args.get("captures") is not None:
        payload["captures"] = coerce_string_list(args.get("captures"), field="captures")
    return payload
