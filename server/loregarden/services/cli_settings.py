"""Workspace CLI runtime settings — adapter and model selection."""

from __future__ import annotations

from dataclasses import dataclass

from loregarden.config import settings
from loregarden.models.domain import Workspace

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
) -> str:
    import os

    env_override = os.environ.get("LOREGARDEN_CLI_ADAPTER")
    if env_override:
        return env_override

    ws = workspace_cli_settings(workspace)
    if ws.cli_adapter and ws.cli_adapter != "default":
        return ws.cli_adapter

    return agent_adapter or settings.cli_adapter


def resolve_claude_model(workspace: Workspace | None) -> str:
    import os

    env_model = os.environ.get("LOREGARDEN_CLAUDE_MODEL")
    if env_model:
        return env_model
    ws = workspace_cli_settings(workspace)
    return ws.claude_model or settings.claude_model


def resolve_cursor_model(workspace: Workspace | None) -> str:
    import os

    env_model = os.environ.get("LOREGARDEN_CURSOR_MODEL")
    if env_model:
        return env_model
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


def runtime_options_payload() -> dict:
    return {
        "cli_adapters": CLI_ADAPTER_OPTIONS,
        "claude_models": CLAUDE_MODEL_OPTIONS,
        "cursor_models": CURSOR_MODEL_OPTIONS,
    }
