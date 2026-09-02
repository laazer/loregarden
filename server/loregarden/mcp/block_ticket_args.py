"""Argument handling for `loregarden_block_ticket`'s prepared action.

Split from `mcp.tools`, which sits on its 1500-line cap. These two functions are
one concern — turning an agent's block arguments into a `PreparedAction` — and
the coercers they need are injected, keeping this module below `mcp.tools` in
the import graph the way `ticket_edit_tools` already is.
"""

from __future__ import annotations

from typing import Any

from loregarden.models.domain import HumanActionTier
from loregarden.services.prepared_action import PreparedAction


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


def prepared_action_from(arguments: dict[str, Any]) -> PreparedAction | None:
    """The prepared action a block carries, or None when it carries none.

    An unknown tier is refused rather than defaulted. Defaulting would quietly
    file work at the rung the agent did not claim, and MANUAL — the rung that
    costs a person the most — is exactly the one a silent default would pick.
    """
    supplied = {
        key: arguments[key]
        for key in ("tier", "attempted", "prepared", "command", "script_path", "captures")
        if arguments.get(key)
    }
    if not supplied:
        return None
    raw_tier = str(supplied.pop("tier", "")).strip()
    try:
        tier = HumanActionTier(raw_tier)
    except ValueError:
        allowed = ", ".join(t.value for t in HumanActionTier)
        raise ValueError(
            f"tier must be one of {allowed} when a prepared action is supplied (got {raw_tier!r})"
        ) from None
    return PreparedAction(tier=tier, **supplied)
