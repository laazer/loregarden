"""Scoped CLI permission allow rules (workspace, ticket, stage)."""

from __future__ import annotations

import json
from typing import Any, Literal

from sqlmodel import Session

from loregarden.models.domain import Ticket, Workspace

PermissionScope = Literal["workspace", "ticket", "stage"]


def normalize_tool_input(tool_input: Any) -> dict[str, Any]:
    if not isinstance(tool_input, dict):
        return {}
    return tool_input


def permission_rule_key(tool_name: str, tool_input: dict[str, Any]) -> str:
    payload = {
        "tool_name": tool_name,
        "tool_input": normalize_tool_input(tool_input),
    }
    return json.dumps(payload, sort_keys=True, ensure_ascii=False)


def load_allowlist(raw_json: str | None) -> list[dict[str, Any]]:
    if not raw_json:
        return []
    try:
        parsed = json.loads(raw_json)
    except json.JSONDecodeError:
        return []
    return parsed if isinstance(parsed, list) else []


def load_workspace_allowlist(workspace: Workspace | None) -> list[dict[str, Any]]:
    if not workspace:
        return []
    return load_allowlist(workspace.permission_allowlist_json)


def load_ticket_allowlist(ticket: Ticket | None) -> list[dict[str, Any]]:
    if not ticket:
        return []
    return load_allowlist(ticket.permission_allowlist_json)


def permission_rule_matches(rule: dict[str, Any], tool_name: str, tool_input: dict[str, Any]) -> bool:
    if not isinstance(rule, dict):
        return False
    if str(rule.get("tool_name") or "") != tool_name:
        return False
    rule_input = normalize_tool_input(rule.get("tool_input"))
    return permission_rule_key(tool_name, rule_input) == permission_rule_key(tool_name, tool_input)


def _rule_stage_key(rule: dict[str, Any]) -> str:
    return str(rule.get("stage_key") or "").strip()


def _rule_already_present(
    rules: list[dict[str, Any]],
    *,
    tool_name: str,
    tool_input: dict[str, Any],
    stage_key: str = "",
) -> bool:
    for rule in rules:
        if not permission_rule_matches(rule, tool_name, tool_input):
            continue
        if _rule_stage_key(rule) == stage_key.strip():
            return True
    return False


def is_permission_allowed(
    session: Session,
    *,
    workspace_id: str,
    ticket_id: str,
    stage_key: str,
    tool_name: str,
    tool_input: dict[str, Any],
) -> PermissionScope | None:
    workspace = session.get(Workspace, workspace_id)
    for rule in load_workspace_allowlist(workspace):
        if permission_rule_matches(rule, tool_name, tool_input):
            return "workspace"

    ticket = session.get(Ticket, ticket_id)
    normalized_stage = stage_key.strip()
    for rule in load_ticket_allowlist(ticket):
        if not permission_rule_matches(rule, tool_name, tool_input):
            continue
        rule_stage = _rule_stage_key(rule)
        if rule_stage and rule_stage != normalized_stage:
            continue
        return "stage" if rule_stage else "ticket"
    return None


def is_workspace_allowed(
    session: Session,
    workspace_id: str,
    tool_name: str,
    tool_input: dict[str, Any],
) -> bool:
    return (
        is_permission_allowed(
            session,
            workspace_id=workspace_id,
            ticket_id="",
            stage_key="",
            tool_name=tool_name,
            tool_input=tool_input,
        )
        == "workspace"
    )


def add_workspace_allow_rule(
    session: Session,
    workspace_id: str,
    tool_name: str,
    tool_input: dict[str, Any],
) -> bool:
    workspace = session.get(Workspace, workspace_id)
    if not workspace:
        raise ValueError("Workspace not found")

    normalized = normalize_tool_input(tool_input)
    rules = load_workspace_allowlist(workspace)
    if _rule_already_present(rules, tool_name=tool_name, tool_input=normalized):
        return False

    rules.append({"tool_name": tool_name, "tool_input": normalized})
    workspace.permission_allowlist_json = json.dumps(rules, ensure_ascii=False)
    session.add(workspace)
    session.commit()
    return True


def add_ticket_allow_rule(
    session: Session,
    ticket_id: str,
    tool_name: str,
    tool_input: dict[str, Any],
    *,
    stage_key: str = "",
) -> bool:
    ticket = session.get(Ticket, ticket_id)
    if not ticket:
        raise ValueError("Ticket not found")

    normalized = normalize_tool_input(tool_input)
    normalized_stage = stage_key.strip()
    rules = load_ticket_allowlist(ticket)
    if _rule_already_present(
        rules,
        tool_name=tool_name,
        tool_input=normalized,
        stage_key=normalized_stage,
    ):
        return False

    rule: dict[str, Any] = {"tool_name": tool_name, "tool_input": normalized}
    if normalized_stage:
        rule["stage_key"] = normalized_stage
    rules.append(rule)
    ticket.permission_allowlist_json = json.dumps(rules, ensure_ascii=False)
    ticket.revision += 1
    ticket.last_updated_by = "permission_allowlist"
    session.add(ticket)
    session.commit()
    return True
