"""CLI auth / spawn failure helpers — turn opaque adapter errors into operator actions."""

from __future__ import annotations

import re

from loregarden.services.cli_settings import ADAPTER_BINARIES, CLI_ADAPTER_OPTIONS

# errno-style: [Errno 2] No such file or directory: 'codex'
# pathlib-ish: No such file or directory: '/opt/bin/codex'
_MISSING_BINARY = re.compile(
    r"no such file or directory:\s*['\"]?([^'\"]+)['\"]?",
    re.IGNORECASE,
)

_ADAPTER_LABELS = {opt["id"]: opt["label"] for opt in CLI_ADAPTER_OPTIONS}


def _binary_to_adapter(binary_name: str) -> tuple[str, str, str] | None:
    """Map a spawned executable back to (adapter_id, label, env_override_key)."""
    base = binary_name.rsplit("/", 1)[-1]
    for adapter, (name, env_key) in ADAPTER_BINARIES.items():
        if base == name or binary_name == name:
            return adapter, _ADAPTER_LABELS.get(adapter, adapter), env_key
    return None


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
    """Return a fix hint when ``detail`` is a Cursor/Claude/LM Studio failure."""
    lower = detail.lower()
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
    return None


def format_agent_unavailable(agent_name: str, exc: BaseException) -> str:
    detail = str(exc).strip() or exc.__class__.__name__
    hint = format_cli_missing_hint(detail, exc) or format_cli_auth_hint(detail)
    if hint:
        return f"{agent_name} unavailable: {hint}\n\nDetails: {detail}"
    return f"{agent_name} unavailable: {detail}"
