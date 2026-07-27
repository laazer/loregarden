"""Workspace CLI runtime settings — adapter and model selection.

One precedence chain, one resolver. Surfaces (stage runs, triage, ticket studio,
terminal handoff) all call ``resolve_model_for_adapter`` for whichever adapter
``resolve_effective_adapter`` selected — they do not each invent their own.
"""

from __future__ import annotations

import json
import os
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
    {"id": "local", "label": "Local stub (dev/tests)"},
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

# Adapters that take a ``--model`` / model-id pin. local/codex do not.
MODEL_PIN_ADAPTERS = frozenset({"claude", "cursor", "lmstudio"})


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
    env_override = os.environ.get("LOREGARDEN_CLI_ADAPTER")
    if env_override:
        return env_override

    if ticket_adapter and ticket_adapter != "default":
        return ticket_adapter

    ws = workspace_cli_settings(workspace)
    if ws.cli_adapter and ws.cli_adapter != "default":
        return ws.cli_adapter

    return agent_adapter or settings.cli_adapter


def _first_set(*values: str) -> str:
    for value in values:
        if value:
            return value
    return ""


def adapter_model_pins_apply(*, agent_adapter: str, selected_adapter: str) -> bool:
    """Whether agent/stage model pins belong to the selected provider.

    Those pins are authored against the agent's declared adapter (Claude aliases
    vs Cursor ids vs an LM Studio load name). When workspace/ticket/env overrides
    the provider, forwarding them would send the wrong namespace to the CLI.
    """
    declared = agent_adapter or ""
    if not declared or declared == "default":
        return True
    return declared == selected_adapter


def ticket_model_for_adapter(
    adapter: str,
    *,
    claude_model: str = "",
    cursor_model: str = "",
    lmstudio_model: str = "",
) -> str:
    """Pick the ticket-runtime model field that matches the selected adapter."""
    if adapter == "claude":
        return claude_model
    if adapter == "cursor":
        return cursor_model
    if adapter == "lmstudio":
        return lmstudio_model
    return ""


def resolve_model_for_adapter(
    adapter: str,
    workspace: Workspace | None,
    *,
    ticket_model: str = "",
    stage_model: str = "",
    agent_model: str = "",
) -> str:
    """Resolve the model id for one concrete adapter.

    Precedence (shared by every surface): env → ticket → stage → agent →
    workspace → global settings. Callers must only pass stage/agent pins that
    belong to this adapter (see ``adapter_model_pins_apply``).
    """
    if adapter not in MODEL_PIN_ADAPTERS:
        return ""

    ws = workspace_cli_settings(workspace)
    if adapter == "claude":
        return _first_set(
            os.environ.get("LOREGARDEN_CLAUDE_MODEL", ""),
            ticket_model,
            stage_model,
            agent_model,
            ws.claude_model,
            settings.claude_model,
        )
    if adapter == "cursor":
        return _first_set(
            os.environ.get("LOREGARDEN_CURSOR_MODEL", ""),
            ticket_model,
            stage_model,
            agent_model,
            ws.cursor_model,
            settings.cursor_model,
        )
    # lmstudio
    return _first_set(
        os.environ.get("LOREGARDEN_LMSTUDIO_MODEL", ""),
        ticket_model,
        stage_model,
        agent_model,
        ws.lmstudio_model,
        settings.lmstudio_model,
    )


def resolve_claude_model(
    workspace: Workspace | None,
    *,
    ticket_model: str = "",
    stage_model: str = "",
    agent_model: str = "",
) -> str:
    return resolve_model_for_adapter(
        "claude",
        workspace,
        ticket_model=ticket_model,
        stage_model=stage_model,
        agent_model=agent_model,
    )


# Models too weak to reliably drive Loregarden MCP tool calls. run_43ea0c: a
# haiku learning agent could not invoke the loregarden_* tools (tried them as
# shell commands, then fell back to hand-writing a LEARNINGS.md file).
WEAK_MCP_CLAUDE_MODELS = ("haiku",)


def weak_mcp_model_warning(model: str, adapter: str) -> str | None:
    """Return a warning if a claude agent that must drive MCP tools is pinned to a
    model too weak to call them reliably; otherwise None."""
    if adapter != "claude" or not model:
        return None
    lowered = model.lower()
    if any(weak in lowered for weak in WEAK_MCP_CLAUDE_MODELS):
        return (
            f"Model '{model}' may be too weak to reliably drive Loregarden MCP tools "
            "(loregarden_* calls). Consider sonnet or stronger for stages that call MCP tools."
        )
    return None


def resolve_cursor_model(
    workspace: Workspace | None,
    *,
    ticket_model: str = "",
    stage_model: str = "",
    agent_model: str = "",
) -> str:
    return resolve_model_for_adapter(
        "cursor",
        workspace,
        ticket_model=ticket_model,
        stage_model=stage_model,
        agent_model=agent_model,
    )


def resolve_lmstudio_base_url(workspace: Workspace | None) -> str:
    env_url = os.environ.get("LOREGARDEN_LMSTUDIO_BASE_URL")
    if env_url:
        return env_url
    ws = workspace_cli_settings(workspace)
    return ws.lmstudio_base_url or settings.lmstudio_base_url


def resolve_lmstudio_model(
    workspace: Workspace | None,
    *,
    ticket_model: str = "",
    stage_model: str = "",
    agent_model: str = "",
) -> str:
    return resolve_model_for_adapter(
        "lmstudio",
        workspace,
        ticket_model=ticket_model,
        stage_model=stage_model,
        agent_model=agent_model,
    )


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


def runtime_options_payload(*, lmstudio_base_url: str = "") -> dict:
    from loregarden.services.lmstudio_discovery import lmstudio_model_options

    return {
        "cli_adapters": CLI_ADAPTER_OPTIONS,
        "claude_models": CLAUDE_MODEL_OPTIONS,
        "cursor_models": CURSOR_MODEL_OPTIONS,
        "lmstudio_models": lmstudio_model_options(lmstudio_base_url),
    }
