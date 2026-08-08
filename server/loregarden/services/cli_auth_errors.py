"""CLI auth / spawn failure helpers — turn opaque adapter errors into operator actions."""

from __future__ import annotations

import re

from loregarden.services.cli_settings import ADAPTER_BINARIES, CLI_ADAPTER_OPTIONS
from loregarden.services.usage_limits import detect_usage_limit, format_usage_limit_hint

# errno-style: [Errno 2] No such file or directory: 'codex'
# pathlib-ish: No such file or directory: '/opt/bin/codex'
_MISSING_BINARY = re.compile(
    r"no such file or directory:\s*['\"]?([^'\"]+)['\"]?",
    re.IGNORECASE,
)

# Codex prints the real failure as: ERROR: {"type":"error",...}
_CODEX_ERROR_START = re.compile(r"ERROR:\s*\{", re.IGNORECASE)

_ADAPTER_LABELS = {opt["id"]: opt["label"] for opt in CLI_ADAPTER_OPTIONS}

# Keep chat failures readable — Codex dumps its TUI + the whole prompt into stderr.
_DETAIL_LIMIT = 480


def _binary_to_adapter(binary_name: str) -> tuple[str, str, str] | None:
    """Map a spawned executable back to (adapter_id, label, env_override_key)."""
    base = binary_name.rsplit("/", 1)[-1]
    for adapter, (name, env_key) in ADAPTER_BINARIES.items():
        if base == name or binary_name == name:
            return adapter, _ADAPTER_LABELS.get(adapter, adapter), env_key
    return None


def _extract_codex_json_errors(detail: str) -> list[str]:
    """Brace-balance Codex ``ERROR: {...}`` payloads (nested JSON breaks a naive regex)."""
    found: list[str] = []
    for match in _CODEX_ERROR_START.finditer(detail):
        start = match.end() - 1
        depth = 0
        for index, char in enumerate(detail[start:], start):
            if char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    found.append(detail[start : index + 1].strip())
                    break
    return found


def _compact_cli_detail(detail: str, *, limit: int = _DETAIL_LIMIT) -> str:
    """Prefer the actionable ERROR line(s); never paste a full Codex TUI dump."""
    text = (detail or "").strip()
    if not text:
        return ""

    json_errors = _extract_codex_json_errors(text)
    if json_errors:
        text = "\n".join(json_errors)
    else:
        error_lines = [
            line.strip()
            for line in text.splitlines()
            if any(
                marker in line.lower()
                for marker in (
                    "error",
                    "invalid_request",
                    "not supported",
                    "authentication",
                    "not logged",
                    "no such file",
                    "usage limit",
                )
            )
        ]
        if error_lines:
            # Last few matter — Codex often prints MCP noise first, then the model 400.
            text = "\n".join(error_lines[-6:])

    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def format_cli_missing_hint(detail: str, exc: BaseException) -> str | None:
    """Return a fix hint when the adapter CLI binary is not on PATH."""
    if not isinstance(exc, FileNotFoundError) and "no such file or directory" not in detail.lower():
        return None

    binary = ""
    if isinstance(exc, FileNotFoundError) and exc.filename:
        binary = str(exc.filename)
    else:
        match = _MISSING_BINARY.search(detail)
        if match:
            binary = match.group(1).strip()

    mapped = _binary_to_adapter(binary) if binary else None
    if mapped:
        adapter, label, env_key = mapped
        return (
            f"The {label} CLI (`{binary}`) is not installed (or not on PATH), so Baxter "
            f"cannot start a `{adapter}` turn.\n\n"
            "Fix:\n"
            f"1. Install the `{binary}` CLI and ensure it is on PATH, or\n"
            f"2. Point Loregarden at it with `{env_key}=/absolute/path/to/{binary}`, then "
            "restart the server, or\n"
            "3. Switch this chat/workspace runtime to an adapter whose CLI is available "
            "(Claude, Cursor, or LM Studio)"
        )

    shown = binary or "the selected CLI"
    return (
        f"Baxter could not find `{shown}` on this machine.\n\n"
        "Fix:\n"
        "1. Install that CLI and ensure it is on PATH, or\n"
        "2. Switch the chat/workspace runtime to an adapter that is installed"
    )


def format_cli_auth_hint(detail: str) -> str | None:
    """Return a fix hint when ``detail`` is a Cursor/Claude/LM Studio/Codex failure."""
    # First: a plan limit is not a misconfiguration, and its wording overlaps the
    # provider markers below (a ChatGPT quota message names chatgpt.com too).
    limit = detect_usage_limit(detail)
    if limit:
        return format_usage_limit_hint(limit)

    lower = detail.lower()
    if "not supported when using codex with a chatgpt account" in lower or (
        "chatgpt account" in lower and ("not supported" in lower or "gpt-5" in lower)
    ):
        return (
            "Codex rejected this model for a ChatGPT-signed-in account "
            "(pins like `gpt-5` are API-only on that path).\n\n"
            "Fix:\n"
            "1. Clear the Codex model pin in this chat's runtime picker (use Default), or\n"
            "2. Sign Codex in with an OpenAI API key instead of ChatGPT login, or\n"
            "3. Switch this chat/workspace adapter to Claude or Cursor"
        )
    if (
        "authentication required" in lower
        or "cursor_api_key" in lower
        or "agent login" in lower
        or ("not authenticated" in lower and "cursor" in lower)
        or ("failed to reach the cursor api" in lower)
    ):
        return (
            "Baxter could not authenticate the Cursor CLI for a headless run "
            "(`cursor-agent -p`).\n\n"
            "Tried, in order: CURSOR_API_KEY env, data/.cursor-api-key, and your "
            "Cursor IDE login token.\n\n"
            "Fix:\n"
            "1. Stay signed into the Cursor IDE on this machine, then restart the server, or\n"
            "2. Create a User API key at https://cursor.com/dashboard/integrations "
            "and run `task cursor:setup-key`, or\n"
            "3. Switch the workspace/triage adapter to Claude or LM Studio"
        )
    if "claude_code_oauth_token" in lower or ("not logged in" in lower and "claude" in lower):
        return (
            "Claude CLI is not authenticated for background runs. "
            "Run `task claude:setup-token`, then restart the server."
        )
    if (
        "lm studio" in lower
        or "no loaded models" in lower
        or ("could not resolve" in lower and "lm studio" in lower)
        or (
            ("connection refused" in lower or "connecterror" in lower or "connect error" in lower)
            and ("1234" in lower or "lmstudio" in lower or "/v1" in lower)
        )
    ):
        return (
            "LM Studio did not answer (or has no model loaded).\n\n"
            "Fix:\n"
            "1. Start LM Studio and load a model,\n"
            "2. Enable the local server (default http://127.0.0.1:1234/v1),\n"
            "3. Set the workspace/triage provider to LM Studio and optionally pin "
            "the loaded model id + server URL in Settings"
        )
    if (
        "jsonrpcmessage" in lower
        or "rmcp::transport" in lower
        or ("deserialize error" in lower and "codex" in lower)
    ):
        return (
            "The Codex CLI failed while initializing MCP (JSON-RPC handshake).\n\n"
            "This often follows a rejected model/auth error above it in the log.\n\n"
            "Fix:\n"
            "1. Clear the Codex model pin or switch adapter to Claude/Cursor, or\n"
            "2. Update the Codex CLI, then retry"
        )
    return None


def format_agent_unavailable(agent_name: str, exc: BaseException) -> str:
    detail = str(exc).strip() or exc.__class__.__name__
    hint = format_cli_missing_hint(detail, exc) or format_cli_auth_hint(detail)
    compact = _compact_cli_detail(detail)
    if hint:
        return f"{agent_name} unavailable: {hint}\n\nDetails: {compact}"
    return f"{agent_name} unavailable: {compact}"
