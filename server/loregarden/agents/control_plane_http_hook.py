"""Claude Code ``PreToolUse`` hook for pipeline agents: no Bash call to this control plane's HTTP API.

A pipeline agent reaches the control plane through its MCP tools, which apply
the orchestrated-agent policy and attribute every call to the run. The same
server answers plain HTTP on the same port, where neither happens. On
2026-10-20 a review lane refused `loregarden_create_ticket` POSTed to
``/api/tickets`` with curl and filed lg-milestone-that-1315 unattributed.

This denies any Bash command naming the control plane's own address: a
loopback host (or the configured one) on its port. Other ports stay open, so an
agent can still drive a sandbox instance or its own dev server. It reads the
command text, so it stops an agent that follows the denial message's old advice
rather than one set on routing around it; `mcp.tool_ids` says the same of the
tool policy it backs up.

Wired in by ``cli_adapters.stage_run_hook_settings``; run as
``python -m loregarden.agents.control_plane_http_hook <api-base-url>``.
"""

from __future__ import annotations

import json
import re
import sys
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, ValidationError

_BASH = "Bash"  # py-org: allow-string - Claude Code tool name

#: Every spelling of "this machine" a URL can use.
_LOOPBACK_HOSTS = ("127.0.0.1", "localhost", "0.0.0.0", "[::1]", "::1")  # noqa: S104 - matched, not bound

DENIAL = (
    "Pipeline agents may not call the Loregarden HTTP API from Bash: it skips the tool policy "
    "and records no run. Use the mcp__loregarden__* tools. If the tool you need is refused, "
    "say so in your stage report; do not route around the refusal."
)


class _ToolInput(BaseModel):
    model_config = ConfigDict(extra="ignore")
    command: str = ""


class HookInput(BaseModel):
    model_config = ConfigDict(extra="ignore")
    tool_name: str = ""
    tool_input: _ToolInput = _ToolInput()


def control_plane_pattern(api_base_url: str) -> re.Pattern[str]:
    """A pattern matching the control plane's host:port in command text."""
    parts = urlsplit(api_base_url)
    port = parts.port or (443 if parts.scheme == "https" else 80)
    hosts = {*_LOOPBACK_HOSTS, parts.hostname or ""} - {""}
    alternatives = "|".join(re.escape(host) for host in sorted(hosts, key=len, reverse=True))
    # The port must end there: :8000 must not match :80001.
    return re.compile(rf"(?:^|[^\w.-])(?:{alternatives}):{port}(?!\d)", re.IGNORECASE)


def denies(hook_input: HookInput, pattern: re.Pattern[str]) -> bool:
    return hook_input.tool_name == _BASH and bool(pattern.search(hook_input.tool_input.command))


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print("usage: control_plane_http_hook <api-base-url>", file=sys.stderr)
        return 2
    try:
        hook_input = HookInput.model_validate_json(sys.stdin.read())
    except ValidationError as exc:
        # Exit 1 is a non-blocking hook error: Claude Code shows it and runs the
        # tool. A hook that cannot read its input must not wedge every Bash call.
        print(f"control_plane_http_hook: unreadable hook input: {exc}", file=sys.stderr)
        return 1
    if denies(hook_input, control_plane_pattern(argv[0])):
        decision = {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": DENIAL,
            }
        }
        print(json.dumps(decision))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
