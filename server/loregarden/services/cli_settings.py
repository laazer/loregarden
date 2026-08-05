"""Workspace CLI runtime settings — adapter, model, and reasoning-effort selection.

One precedence chain, one resolver. Surfaces (stage runs, triage, ticket studio,
terminal handoff) all call ``resolve_model_for_adapter`` and
``resolve_effort_for_adapter`` for whichever adapter ``resolve_effective_adapter``
selected — they do not each invent their own.

Effort is stored per adapter rather than shared, because the ladders differ
(`xhigh` means nothing to LM Studio) and so does the delivery mechanism: Claude
Code has a `--effort` flag, cursor takes a bracket parameter on a parameterized
model id, and LM Studio reads OpenAI's `reasoning_effort` request field.
"""

from __future__ import annotations

import json
import os
import shutil
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
    {"id": "codex", "label": "Codex CLI"},
    {"id": "lmstudio", "label": "LM Studio"},
]

# The executable each adapter spawns, and the env key that overrides its path.
# ``default``/``local``/``lmstudio`` spawn nothing local, so they are always
# available. Everything else is only selectable if the CLI is actually on PATH —
# picking one that is not turns every run into a raw ``FileNotFoundError``.
ADAPTER_BINARIES: dict[str, tuple[str, str]] = {
    "claude": ("claude", "LOREGARDEN_CLAUDE_BIN"),
    "cursor": ("cursor-agent", "LOREGARDEN_CURSOR_BIN"),
    "codex": ("codex", "LOREGARDEN_CODEX_BIN"),
}


def adapter_available(adapter: str) -> bool:
    """Whether the adapter's CLI can be spawned on this machine."""
    binary = ADAPTER_BINARIES.get(adapter)
    if binary is None:
        return True
    name, env_key = binary
    override = os.environ.get(env_key)
    if override:
        return bool(shutil.which(override) or os.path.exists(override))
    return shutil.which(name) is not None


def cli_adapter_options() -> list[dict[str, str | bool]]:
    """Adapter catalogue annotated with local CLI availability."""
    return [{**opt, "available": adapter_available(opt["id"])} for opt in CLI_ADAPTER_OPTIONS]


# Pinned ids are what `claude --model` accepts: a floating alias, or a model's
# full name. Aliases track the newest release in their tier; a pinned id keeps a
# run reproducible across a model launch. Retired ids are deliberately absent —
# a pin the CLI can no longer resolve fails the run, so they are worse than the
# alias they used to mean.
CLAUDE_MODEL_OPTIONS: list[dict[str, str]] = [
    {"id": "", "label": "Default (Claude Code profile)"},
    {"id": "opus", "label": "Opus — latest alias"},
    {"id": "sonnet", "label": "Sonnet — latest alias"},
    {"id": "haiku", "label": "Haiku — latest alias"},
    {"id": "fable", "label": "Fable — latest alias"},
    {"id": "claude-opus-5", "label": "Claude Opus 5 (pinned)"},
    {"id": "claude-sonnet-5", "label": "Claude Sonnet 5 (pinned)"},
    {"id": "claude-fable-5", "label": "Claude Fable 5 (pinned)"},
    {"id": "claude-opus-4-8", "label": "Claude Opus 4.8 (pinned)"},
    {"id": "claude-haiku-4-5-20251001", "label": "Claude Haiku 4.5 (pinned)"},
]

# ``supports_effort`` marks cursor's *parameterized* models — the only ones whose
# id accepts a bracket override (``claude-opus-4-8[effort=high]``, per
# `cursor-agent --help`). Appending brackets to a plain id like ``gpt-5`` makes
# the CLI reject the model, so the effort pin is gated on this flag rather than
# applied to whatever id happens to be selected.
CURSOR_MODEL_OPTIONS: list[dict[str, str | bool]] = [
    {"id": "", "label": "Default (Cursor profile)", "supports_effort": False},
    {"id": "claude-opus-4-8", "label": "Claude Opus 4.8", "supports_effort": True},
    {"id": "sonnet-4", "label": "Sonnet 4", "supports_effort": False},
    {"id": "sonnet-4-thinking", "label": "Sonnet 4 Thinking", "supports_effort": False},
    {"id": "gpt-5", "label": "GPT-5", "supports_effort": False},
]

CURSOR_EFFORT_MODELS = frozenset(
    str(opt["id"]) for opt in CURSOR_MODEL_OPTIONS if opt.get("supports_effort")
)

CODEX_MODEL_OPTIONS: list[dict[str, str]] = [
    {"id": "", "label": "Default (Codex profile)"},
    {"id": "gpt-5", "label": "GPT-5"},
]

# Effort ladders differ by provider, so they are catalogued per adapter rather
# than shared. Claude Code takes `--effort` natively; cursor expresses it as a
# bracket parameter on a parameterized model id; LM Studio is OpenAI-compatible,
# where the field is `reasoning_effort` and only the classic three levels are
# standard.
CLAUDE_EFFORT_OPTIONS: list[dict[str, str]] = [
    {"id": "", "label": "Default (Claude Code decides)"},
    {"id": "low", "label": "Low — scoped, latency-sensitive work"},
    {"id": "medium", "label": "Medium — cost-conscious balance"},
    {"id": "high", "label": "High — Claude Code default"},
    {"id": "xhigh", "label": "Extra high — best for coding/agentic"},
    {"id": "max", "label": "Max — correctness over cost"},
]

CURSOR_EFFORT_OPTIONS: list[dict[str, str]] = [
    {"id": "", "label": "Default (Cursor decides)"},
    {"id": "low", "label": "Low"},
    {"id": "medium", "label": "Medium"},
    {"id": "high", "label": "High"},
]

LMSTUDIO_EFFORT_OPTIONS: list[dict[str, str]] = [
    {"id": "", "label": "Default (model's own setting)"},
    {"id": "low", "label": "Low"},
    {"id": "medium", "label": "Medium"},
    {"id": "high", "label": "High"},
]

EFFORT_OPTIONS_BY_ADAPTER: dict[str, list[dict[str, str]]] = {
    "claude": CLAUDE_EFFORT_OPTIONS,
    "cursor": CURSOR_EFFORT_OPTIONS,
    "lmstudio": LMSTUDIO_EFFORT_OPTIONS,
}

VALID_CLI_ADAPTERS = {opt["id"] for opt in CLI_ADAPTER_OPTIONS}

# Adapters that take a ``--model`` / model-id pin. local does not.
MODEL_PIN_ADAPTERS = frozenset({"claude", "cursor", "codex", "lmstudio"})

# Adapters with a reasoning-effort control. Same set today, but the two are
# distinct concepts — keep them separate so adding a model-only adapter does not
# silently claim effort support.
EFFORT_PIN_ADAPTERS = frozenset(EFFORT_OPTIONS_BY_ADAPTER)


@dataclass(frozen=True)
class WorkspaceCliSettings:
    cli_adapter: str = "default"
    claude_model: str = ""
    cursor_model: str = ""
    codex_model: str = ""
    lmstudio_base_url: str = ""
    lmstudio_model: str = ""
    claude_effort: str = ""
    cursor_effort: str = ""
    lmstudio_effort: str = ""


def workspace_cli_settings(workspace: Workspace | None) -> WorkspaceCliSettings:
    if not workspace:
        return WorkspaceCliSettings()
    return WorkspaceCliSettings(
        cli_adapter=workspace.cli_adapter or "default",
        claude_model=workspace.claude_model or "",
        cursor_model=workspace.cursor_model or "",
        codex_model=workspace.codex_model or "",
        lmstudio_base_url=workspace.lmstudio_base_url or "",
        lmstudio_model=workspace.lmstudio_model or "",
        claude_effort=workspace.claude_effort or "",
        cursor_effort=workspace.cursor_effort or "",
        lmstudio_effort=workspace.lmstudio_effort or "",
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
    codex_model: str = "",
    lmstudio_model: str = "",
) -> str:
    """Pick the ticket-runtime model field that matches the selected adapter."""
    if adapter == "claude":
        return claude_model
    if adapter == "cursor":
        return cursor_model
    if adapter == "codex":
        return codex_model
    if adapter == "lmstudio":
        return lmstudio_model
    return ""


def ticket_effort_for_adapter(
    adapter: str,
    *,
    claude_effort: str = "",
    cursor_effort: str = "",
    lmstudio_effort: str = "",
) -> str:
    """Pick the ticket-runtime effort field that matches the selected adapter."""
    if adapter == "claude":
        return claude_effort
    if adapter == "cursor":
        return cursor_effort
    if adapter == "lmstudio":
        return lmstudio_effort
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
    if adapter == "codex":
        return _first_set(
            os.environ.get("LOREGARDEN_CODEX_MODEL", ""),
            ticket_model,
            stage_model,
            agent_model,
            ws.codex_model,
            settings.codex_model,
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


def resolve_effort_for_adapter(
    adapter: str,
    workspace: Workspace | None,
    *,
    ticket_effort: str = "",
) -> str:
    """Resolve the reasoning-effort level for one concrete adapter.

    Same precedence chain as ``resolve_model_for_adapter`` minus the stage/agent
    tiers, which carry no effort pin. An unsupported level is dropped rather than
    forwarded: the CLIs reject an unknown value outright, and silently running at
    the provider default beats failing every run in the workspace.
    """
    if adapter not in EFFORT_PIN_ADAPTERS:
        return ""

    ws = workspace_cli_settings(workspace)
    env_key = f"LOREGARDEN_{adapter.upper()}_EFFORT"
    if adapter == "claude":
        resolved = _first_set(
            os.environ.get(env_key, ""), ticket_effort, ws.claude_effort, settings.claude_effort
        )
    elif adapter == "cursor":
        resolved = _first_set(
            os.environ.get(env_key, ""), ticket_effort, ws.cursor_effort, settings.cursor_effort
        )
    else:
        resolved = _first_set(
            os.environ.get(env_key, ""),
            ticket_effort,
            ws.lmstudio_effort,
            settings.lmstudio_effort,
        )

    valid = {opt["id"] for opt in EFFORT_OPTIONS_BY_ADAPTER[adapter]}
    return resolved if resolved in valid else ""


def apply_cursor_effort(model: str, effort: str) -> str:
    """Fold an effort level into a cursor model id as a bracket parameter.

    Cursor has no `--effort` flag; a parameterized model takes overrides inline
    (``claude-opus-4-8[effort=high]``). Only ids known to be parameterized are
    rewritten — brackets on a plain id make `cursor-agent` reject the model — and
    an id the operator already bracketed themselves is left alone.
    """
    if not model or not effort or "[" in model:
        return model
    if model not in CURSOR_EFFORT_MODELS:
        return model
    return f"{model}[effort={effort}]"


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


RUNTIME_OVERRIDE_FIELDS = (
    "claude_model",
    "cursor_model",
    "codex_model",
    "lmstudio_base_url",
    "lmstudio_model",
    "claude_effort",
    "cursor_effort",
    "lmstudio_effort",
)


def parse_runtime_settings(runtime_json: str) -> WorkspaceRuntimeSettings:
    """Read one of the `*_runtime_json` override blobs (orchestration, triage, studio…)."""
    data = json.loads(runtime_json or "{}")
    return WorkspaceRuntimeSettings(
        cli_adapter=str(data.get("cli_adapter") or "default"),
        claude_model=str(data.get("claude_model") or ""),
        cursor_model=str(data.get("cursor_model") or ""),
        codex_model=str(data.get("codex_model") or ""),
        lmstudio_base_url=str(data.get("lmstudio_base_url") or ""),
        lmstudio_model=str(data.get("lmstudio_model") or ""),
        claude_effort=str(data.get("claude_effort") or ""),
        cursor_effort=str(data.get("cursor_effort") or ""),
        lmstudio_effort=str(data.get("lmstudio_effort") or ""),
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
    for field in RUNTIME_OVERRIDE_FIELDS:
        value = str(overrides.get(field) or "").strip()
        if value:
            data[field] = value
    return Workspace.model_validate(data)


def validated_effort_pins(body: WorkspaceRuntimeUpdate) -> dict[str, str]:
    """The three effort pins, stripped, or ``ValueError`` naming the bad field.

    Rejecting at the write rather than at the run: the resolver drops an unknown
    level silently, which is the right thing mid-run but would leave an operator
    staring at a saved setting that never takes effect.
    """
    pins = {
        "claude_effort": body.claude_effort.strip(),
        "cursor_effort": body.cursor_effort.strip(),
        "lmstudio_effort": body.lmstudio_effort.strip(),
    }
    for field, value in pins.items():
        adapter = field.removesuffix("_effort")
        if value not in {opt["id"] for opt in EFFORT_OPTIONS_BY_ADAPTER[adapter]}:
            raise ValueError(f"Invalid {field}: {value}")
    return pins


def get_ticket_orchestration_runtime(ticket: Ticket) -> WorkspaceRuntimeSettings:
    return parse_runtime_settings(ticket.orchestration_runtime_json)


def set_ticket_orchestration_runtime(
    session: Session,
    ticket: Ticket,
    body: WorkspaceRuntimeUpdate,
) -> WorkspaceRuntimeSettings:
    if body.cli_adapter not in VALID_CLI_ADAPTERS:
        raise ValueError(f"Invalid cli_adapter: {body.cli_adapter}")
    efforts = validated_effort_pins(body)
    payload = {
        "cli_adapter": body.cli_adapter,
        "claude_model": body.claude_model.strip(),
        "cursor_model": body.cursor_model.strip(),
        "codex_model": body.codex_model.strip(),
        "lmstudio_base_url": body.lmstudio_base_url.strip(),
        "lmstudio_model": body.lmstudio_model.strip(),
        **efforts,
    }
    ticket.orchestration_runtime_json = json.dumps(payload)
    ticket.updated_at = datetime.now(timezone.utc)
    session.add(ticket)
    session.commit()
    session.refresh(ticket)
    return get_ticket_orchestration_runtime(ticket)


def _effective_source(*tiers: tuple[str, str]) -> tuple[str, str]:
    """First (value, source) pair with a non-empty value, else ("", "cli-default")."""
    for value, source in tiers:
        if value:
            return value, source
    return "", "cli-default"


def resolve_runtime_effective(
    workspace: Workspace | None,
    *,
    ticket_runtime: WorkspaceRuntimeSettings | None = None,
) -> dict:
    """What a run started right now would actually invoke, and which tier decided it.

    The selects show what an operator *pinned*, which is usually "Default" — that
    label alone never says which model the CLI will pick or where the choice came
    from. This reports the resolved values so the UI can show the run that would
    happen instead of the empty pin that hides it.
    """
    ticket = ticket_runtime or WorkspaceRuntimeSettings()
    ws = workspace_cli_settings(workspace)

    adapter, adapter_source = _effective_source(
        (os.environ.get("LOREGARDEN_CLI_ADAPTER", ""), "env"),
        ("" if ticket.cli_adapter == "default" else ticket.cli_adapter, "ticket"),
        ("" if ws.cli_adapter == "default" else ws.cli_adapter, "workspace"),
        (settings.cli_adapter, "global"),
    )

    model = resolve_model_for_adapter(
        adapter,
        workspace,
        ticket_model=ticket_model_for_adapter(
            adapter,
            claude_model=ticket.claude_model,
            cursor_model=ticket.cursor_model,
            codex_model=ticket.codex_model,
            lmstudio_model=ticket.lmstudio_model,
        ),
    )
    effort = resolve_effort_for_adapter(
        adapter,
        workspace,
        ticket_effort=ticket_effort_for_adapter(
            adapter,
            claude_effort=ticket.claude_effort,
            cursor_effort=ticket.cursor_effort,
            lmstudio_effort=ticket.lmstudio_effort,
        ),
    )

    # Which tier supplied the model/effort, recomputed the same way the resolvers
    # walk their chains. Stage and agent pins are excluded: they belong to a run
    # that does not exist yet.
    model_source = _effective_source(
        (os.environ.get(f"LOREGARDEN_{adapter.upper()}_MODEL", ""), "env"),
        (
            ticket_model_for_adapter(
                adapter,
                claude_model=ticket.claude_model,
                cursor_model=ticket.cursor_model,
                codex_model=ticket.codex_model,
                lmstudio_model=ticket.lmstudio_model,
            ),
            "ticket",
        ),
        (
            ticket_model_for_adapter(
                adapter,
                claude_model=ws.claude_model,
                cursor_model=ws.cursor_model,
                codex_model=ws.codex_model,
                lmstudio_model=ws.lmstudio_model,
            ),
            "workspace",
        ),
        (model, "global"),
    )[1]
    effort_source = _effective_source(
        (os.environ.get(f"LOREGARDEN_{adapter.upper()}_EFFORT", ""), "env"),
        (
            ticket_effort_for_adapter(
                adapter,
                claude_effort=ticket.claude_effort,
                cursor_effort=ticket.cursor_effort,
                lmstudio_effort=ticket.lmstudio_effort,
            ),
            "ticket",
        ),
        (
            ticket_effort_for_adapter(
                adapter,
                claude_effort=ws.claude_effort,
                cursor_effort=ws.cursor_effort,
                lmstudio_effort=ws.lmstudio_effort,
            ),
            "workspace",
        ),
        (effort, "global"),
    )[1]

    return {
        "cli_adapter": adapter,
        "cli_adapter_source": adapter_source,
        "model": model,
        "model_source": model_source if model else "cli-default",
        "effort": effort,
        "effort_source": effort_source if effort else "cli-default",
        "supports_model": adapter in MODEL_PIN_ADAPTERS,
        "supports_effort": adapter in EFFORT_PIN_ADAPTERS,
    }


def runtime_options_payload(
    *, lmstudio_base_url: str = "", workspace: Workspace | None = None
) -> dict:
    from loregarden.services.lmstudio_discovery import lmstudio_model_options

    return {
        "cli_adapters": cli_adapter_options(),
        "claude_models": CLAUDE_MODEL_OPTIONS,
        "cursor_models": CURSOR_MODEL_OPTIONS,
        "codex_models": CODEX_MODEL_OPTIONS,
        "lmstudio_models": lmstudio_model_options(lmstudio_base_url),
        "claude_efforts": CLAUDE_EFFORT_OPTIONS,
        "cursor_efforts": CURSOR_EFFORT_OPTIONS,
        "lmstudio_efforts": LMSTUDIO_EFFORT_OPTIONS,
        "cursor_effort_models": sorted(CURSOR_EFFORT_MODELS),
        "effective": resolve_runtime_effective(workspace),
    }
