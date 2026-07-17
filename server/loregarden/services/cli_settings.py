"""Workspace CLI runtime settings — adapter and model selection."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone

from loregarden.config import settings
from loregarden.models.domain import (
    Ticket,
    Workspace,
    WorkspaceRuntimeSettings,
    WorkspaceRuntimeUpdate,
)
from sqlmodel import Session

CLI_ADAPTER_OPTIONS: list[dict[str, str]] = [
    {"id": "default", "label": "Workspace default"},
    {"id": "local", "label": "Local runner (dev)"},
    {"id": "claude", "label": "Claude Code"},
    {"id": "cursor", "label": "Cursor Agent"},
    {"id": "lmstudio", "label": "LM Studio"},
]

CLAUDE_MODEL_OPTIONS: list[dict[str, str]] = [
    {"id": "", "label": "Default (Claude Code profile)"},
    {"id": "sonnet", "label": "Sonnet (latest alias)"},
    {"id": "opus", "label": "Opus (latest alias)"},
    {"id": "haiku", "label": "Haiku (latest alias)"},
    {"id": "claude-sonnet-4-20250514", "label": "Claude Sonnet 4"},
    {"id": "claude-opus-4-20250514", "label": "Claude Opus 4"},
]

CURSOR_MODEL_OPTIONS: list[dict[str, str]] = [
    {"id": "", "label": "Default (Cursor profile)"},
    {"id": "sonnet-4", "label": "Sonnet 4"},
    {"id": "gpt-5", "label": "GPT-5"},
    {"id": "sonnet-4-thinking", "label": "Sonnet 4 Thinking"},
]

VALID_CLI_ADAPTERS = {opt["id"] for opt in CLI_ADAPTER_OPTIONS}


@dataclass(frozen=True)
class WorkspaceCliSettings:
    cli_adapter: str = "default"
    claude_model: str = ""
    cursor_model: str = ""
    lmstudio_base_url: str = ""
    lmstudio_model: str = ""


def workspace_cli_settings(workspace: Workspace | None) -> WorkspaceCliSettings:
    if not workspace:
        return WorkspaceCliSettings()
    return WorkspaceCliSettings(
        cli_adapter=workspace.cli_adapter or "default",
        claude_model=workspace.claude_model or "",
        cursor_model=workspace.cursor_model or "",
        lmstudio_base_url=workspace.lmstudio_base_url or "",
        lmstudio_model=workspace.lmstudio_model or "",
    )


def resolve_effective_adapter(
    *,
    agent_adapter: str,
    workspace: Workspace | None,
    ticket_adapter: str = "default",
) -> str:
    import os

    env_override = os.environ.get("LOREGARDEN_CLI_ADAPTER")
    if env_override:
        return env_override

    if ticket_adapter and ticket_adapter != "default":
        return ticket_adapter

    ws = workspace_cli_settings(workspace)
    if ws.cli_adapter and ws.cli_adapter != "default":
        return ws.cli_adapter

    return agent_adapter or settings.cli_adapter


def resolve_claude_model(
    workspace: Workspace | None,
    *,
    ticket_model: str = "",
    stage_model: str = "",
    agent_model: str = "",
) -> str:
    import os

    env_model = os.environ.get("LOREGARDEN_CLAUDE_MODEL")
    if env_model:
        return env_model
    if ticket_model:
        return ticket_model
    if stage_model:
        return stage_model
    if agent_model:
        return agent_model
    ws = workspace_cli_settings(workspace)
    return ws.claude_model or settings.claude_model


def resolve_cursor_model(
    workspace: Workspace | None,
    *,
    ticket_model: str = "",
    stage_model: str = "",
    agent_model: str = "",
) -> str:
    import os

    env_model = os.environ.get("LOREGARDEN_CURSOR_MODEL")
    if env_model:
        return env_model
    if ticket_model:
        return ticket_model
    if stage_model:
        return stage_model
    if agent_model:
        return agent_model
    ws = workspace_cli_settings(workspace)
    return ws.cursor_model or settings.cursor_model


def resolve_lmstudio_base_url(workspace: Workspace | None) -> str:
    import os

    env_url = os.environ.get("LOREGARDEN_LMSTUDIO_BASE_URL")
    if env_url:
        return env_url
    ws = workspace_cli_settings(workspace)
    return ws.lmstudio_base_url or settings.lmstudio_base_url


def resolve_lmstudio_model(workspace: Workspace | None) -> str:
    import os

    env_model = os.environ.get("LOREGARDEN_LMSTUDIO_MODEL")
    if env_model:
        return env_model
    ws = workspace_cli_settings(workspace)
    return ws.lmstudio_model or settings.lmstudio_model


RUNTIME_MODEL_FIELDS = ("claude_model", "cursor_model", "lmstudio_base_url", "lmstudio_model")


def parse_runtime_settings(runtime_json: str) -> WorkspaceRuntimeSettings:
    """Read one of the `*_runtime_json` override blobs (orchestration, triage, studio…)."""
    data = json.loads(runtime_json or "{}")
    return WorkspaceRuntimeSettings(
        cli_adapter=str(data.get("cli_adapter") or "default"),
        claude_model=str(data.get("claude_model") or ""),
        cursor_model=str(data.get("cursor_model") or ""),
        lmstudio_base_url=str(data.get("lmstudio_base_url") or ""),
        lmstudio_model=str(data.get("lmstudio_model") or ""),
    )


def apply_runtime_overrides(workspace: Workspace, runtime_json: str) -> Workspace:
    """Layer a runtime override blob onto a workspace, ignoring unset fields.

    Returns a copy: callers pass the result to an agent invocation rather than persisting it,
    so the stored workspace defaults stay intact.
    """
    overrides = json.loads(runtime_json or "{}")
    if not overrides:
        return workspace
    data = workspace.model_dump()
    adapter = str(overrides.get("cli_adapter") or "default")
    if adapter != "default":
        data["cli_adapter"] = adapter
    for field in RUNTIME_MODEL_FIELDS:
        value = str(overrides.get(field) or "").strip()
        if value:
            data[field] = value
    return Workspace.model_validate(data)


def get_ticket_orchestration_runtime(ticket: Ticket) -> WorkspaceRuntimeSettings:
    return parse_runtime_settings(ticket.orchestration_runtime_json)


def set_ticket_orchestration_runtime(
    session: Session,
    ticket: Ticket,
    body: WorkspaceRuntimeUpdate,
) -> WorkspaceRuntimeSettings:
    if body.cli_adapter not in VALID_CLI_ADAPTERS:
        raise ValueError(f"Invalid cli_adapter: {body.cli_adapter}")
    payload = {
        "cli_adapter": body.cli_adapter,
        "claude_model": body.claude_model.strip(),
        "cursor_model": body.cursor_model.strip(),
        "lmstudio_base_url": body.lmstudio_base_url.strip(),
        "lmstudio_model": body.lmstudio_model.strip(),
    }
    ticket.orchestration_runtime_json = json.dumps(payload)
    ticket.updated_at = datetime.now(timezone.utc)
    session.add(ticket)
    session.commit()
    session.refresh(ticket)
    return get_ticket_orchestration_runtime(ticket)


def runtime_options_payload() -> dict:
    return {
        "cli_adapters": CLI_ADAPTER_OPTIONS,
        "claude_models": CLAUDE_MODEL_OPTIONS,
        "cursor_models": CURSOR_MODEL_OPTIONS,
    }
