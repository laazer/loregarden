"""`loregarden_list_ui_actions` / `loregarden_invoke_ui_action`: drive the open UI.

An agent lists what the operator's open tab can do, then asks it to do one
thing. Approval is decided before the call reaches here, by the invoked
action's catalog effect (`tool_auto_approve._argument_gated_auto_approval`):
view changes auto-approve, writes are gated like any write tool. Human-only
actions are refused here, unconditionally — an auto_approve run included.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ValidationError
from sqlmodel import Session

from loregarden.mcp.tool_ids import McpTool
from loregarden.services.ui_action_broker import UiActionFailure, UiActionOutcome, ui_action_broker
from loregarden.services.ui_action_catalog import CATALOG, UiAction, UiActionEffect

TOOL_DEFINITIONS: list[dict[str, Any]] = [
    {
        "name": McpTool.LIST_UI_ACTIONS,
        "description": (
            "List the actions you can ask the operator's open Loregarden tab to perform "
            "(navigate, open a ticket, edit the open ticket…), each with its effect — "
            "`view` runs without approval, `write` is approved like any write tool, "
            "`human_only` is never available to an agent — its argument schema, and "
            "which open tabs offer it right now. Ticket actions are offered only while "
            "that ticket is open."
        ),
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": McpTool.INVOKE_UI_ACTION,
        "description": (
            "Ask the operator's open Loregarden tab to perform one action from "
            "loregarden_list_ui_actions, as if the operator had used the control. The "
            "operator sees it happen. Fails by name when no tab is open, the action is "
            "not available in any open tab, or the tab does not answer."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": [action.value for action in UiAction],
                    "description": "The action to perform.",
                },
                "arguments": {
                    "type": "object",
                    "description": "The action's arguments, per its schema in the list.",
                },
            },
            "required": ["action"],
            "additionalProperties": False,
        },
    },
]


class UiActionListing(BaseModel):
    actions: list[dict[str, Any]]
    open_tabs: int


def list_ui_actions(_session: Session, _arguments: dict[str, Any]) -> str:
    availability = ui_action_broker.availability()
    actions = [
        {
            **spec.describe(),
            "offered_by_tabs": sorted(
                tab for tab, names in availability.items() if spec.action.value in names
            ),
        }
        for spec in CATALOG.values()
    ]
    return UiActionListing(actions=actions, open_tabs=len(availability)).model_dump_json(indent=2)


def invoke_ui_action(_session: Session, arguments: dict[str, Any]) -> str:
    return _invoke(arguments).model_dump_json(indent=2)


def _invoke(arguments: dict[str, Any]) -> UiActionOutcome:
    name = str(arguments.get("action") or "")
    action = UiAction.try_parse(name)
    if action is None:
        return UiActionOutcome.failed(
            name,
            UiActionFailure.UNKNOWN_ACTION,
            f"no UI action named {name!r}; loregarden_list_ui_actions lists them",
        )
    spec = CATALOG[action]
    if spec.effect is UiActionEffect.HUMAN_ONLY:
        return UiActionOutcome.failed(
            name, UiActionFailure.HUMAN_ONLY, f"{name} is reserved for a person: {spec.description}"
        )
    try:
        parsed = spec.args_model.model_validate(arguments.get("arguments") or {})
    except ValidationError as exc:
        return UiActionOutcome.failed(name, UiActionFailure.INVALID_ARGUMENTS, str(exc))
    return ui_action_broker.invoke_from_thread(
        action, parsed.model_dump(mode="json", exclude_none=True)
    )


HANDLERS = {
    McpTool.LIST_UI_ACTIONS.value: list_ui_actions,
    McpTool.INVOKE_UI_ACTION.value: invoke_ui_action,
}
