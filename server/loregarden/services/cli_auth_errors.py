"""CLI auth failure helpers — turn opaque adapter errors into operator actions."""

from __future__ import annotations


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
    hint = format_cli_auth_hint(detail)
    if hint:
        return f"{agent_name} unavailable: {hint}\n\nDetails: {detail}"
    return f"{agent_name} unavailable: {detail}"
