"""Whether a registered MCP server actually answers.

A server can be registered, enabled, and completely unreachable — a URL that
moved, a command that is not installed, a credential that was never exported.
Nothing found that out until an agent tried to use it mid-run, which is the
most expensive moment to discover it.

The check is a real MCP `initialize` handshake rather than a ping. A URL that
returns 200 for anything, or a command that starts and does nothing, is not a
working MCP server, and a check that called either healthy would be worse than
no check at all.

Deliberately out of the request path: this runs when an operator asks, and
records what it found. It is not a proxy and never sits between an agent and a
server.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime, timezone

import httpx
from loregarden.models.domain import McpServer
from loregarden.services.mcp_registry import parse_args
from sqlmodel import Session

logger = logging.getLogger(__name__)

#: A server that has not answered by now is not usable inside a run either.
TIMEOUT_SECONDS = 8.0

_INITIALIZE = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2024-11-05",
        "capabilities": {},
        "clientInfo": {"name": "loregarden-health", "version": "0.1.0"},
    },
}


@dataclass
class HealthResult:
    ok: bool
    latency_ms: int
    #: Empty when ok. Otherwise what an operator would need to fix it.
    error: str
    #: Server name reported by the handshake, when it gave one.
    server_name: str = ""


def _handshake_name(payload: dict) -> str:
    result = payload.get("result") or {}
    info = result.get("serverInfo") or {}
    return str(info.get("name") or "")


def _looks_like_initialize_result(payload: dict) -> bool:
    """Whether this is an MCP initialize response rather than any old JSON.

    Checked structurally: a JSON-RPC error, or a 200 from something that is not
    an MCP server at all, must not read as healthy.
    """
    if payload.get("error"):
        return False
    result = payload.get("result")
    if not isinstance(result, dict):
        return False
    return "protocolVersion" in result or "capabilities" in result or "serverInfo" in result


def _check_http(server: McpServer) -> HealthResult:
    if not server.url:
        return HealthResult(ok=False, latency_ms=0, error="No URL configured")

    headers = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
    if server.auth_env_var:
        token = os.environ.get(server.auth_env_var, "")
        if not token:
            return HealthResult(
                ok=False,
                latency_ms=0,
                error=f"{server.auth_env_var} is not set where Loregarden runs",
            )
        headers["Authorization"] = f"Bearer {token}"

    started = time.monotonic()
    try:
        # Redirects are not followed: a registered URL that redirects elsewhere
        # is a change the operator should see, not one to quietly chase.
        response = httpx.post(
            server.url,
            json=_INITIALIZE,
            headers=headers,
            timeout=TIMEOUT_SECONDS,
            follow_redirects=False,
        )
    except httpx.TimeoutException:
        return HealthResult(
            ok=False,
            latency_ms=int((time.monotonic() - started) * 1000),
            error=f"No response within {TIMEOUT_SECONDS:.0f}s",
        )
    except httpx.HTTPError as exc:
        return HealthResult(
            ok=False, latency_ms=int((time.monotonic() - started) * 1000), error=str(exc)
        )

    latency_ms = int((time.monotonic() - started) * 1000)
    if response.status_code >= 400:
        return HealthResult(
            ok=False,
            latency_ms=latency_ms,
            error=f"HTTP {response.status_code}",
        )

    payload = _first_json_payload(response.text)
    if payload is None:
        return HealthResult(ok=False, latency_ms=latency_ms, error="Response was not JSON-RPC")
    if not _looks_like_initialize_result(payload):
        detail = (payload.get("error") or {}).get("message") if payload.get("error") else ""
        return HealthResult(
            ok=False,
            latency_ms=latency_ms,
            error=detail or "Did not answer an MCP initialize",
        )
    return HealthResult(
        ok=True, latency_ms=latency_ms, error="", server_name=_handshake_name(payload)
    )


def _first_json_payload(body: str) -> dict | None:
    """The first JSON object in a response body.

    Streamable-HTTP servers answer in SSE frames (`data: {...}`), so the body is
    not always bare JSON.
    """
    text = body.strip()
    if not text:
        return None
    try:
        parsed = json.loads(text)
        return parsed if isinstance(parsed, dict) else None
    except json.JSONDecodeError:
        pass
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("data:"):
            continue
        try:
            parsed = json.loads(line[len("data:") :].strip())
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed
    return None


def _check_stdio(server: McpServer) -> HealthResult:
    if not server.command:
        return HealthResult(ok=False, latency_ms=0, error="No command configured")

    env = dict(os.environ)
    if server.auth_env_var and not env.get(server.auth_env_var):
        return HealthResult(
            ok=False,
            latency_ms=0,
            error=f"{server.auth_env_var} is not set where Loregarden runs",
        )

    argv = [server.command, *parse_args(server.args_json)]
    started = time.monotonic()
    proc = None
    try:
        proc = subprocess.Popen(  # noqa: S603 - argv is operator-configured, not user input
            argv,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
            text=True,
        )
        stdout, stderr = proc.communicate(json.dumps(_INITIALIZE) + "\n", timeout=TIMEOUT_SECONDS)
    except FileNotFoundError:
        return HealthResult(ok=False, latency_ms=0, error=f"Command not found: {server.command}")
    except subprocess.TimeoutExpired:
        if proc:
            proc.kill()
            proc.communicate()
        return HealthResult(
            ok=False,
            latency_ms=int((time.monotonic() - started) * 1000),
            error=f"No response within {TIMEOUT_SECONDS:.0f}s",
        )
    except OSError as exc:
        return HealthResult(ok=False, latency_ms=0, error=str(exc))

    latency_ms = int((time.monotonic() - started) * 1000)
    payload = _first_json_payload(stdout)
    if payload is None:
        detail = (stderr or "").strip().splitlines()
        return HealthResult(
            ok=False,
            latency_ms=latency_ms,
            error=detail[-1] if detail else "No JSON-RPC response on stdout",
        )
    if not _looks_like_initialize_result(payload):
        return HealthResult(
            ok=False, latency_ms=latency_ms, error="Did not answer an MCP initialize"
        )
    return HealthResult(
        ok=True, latency_ms=latency_ms, error="", server_name=_handshake_name(payload)
    )


def check_server(server: McpServer) -> HealthResult:
    """Reach one registered server and report what happened.

    Never raises: a check that blew up would be indistinguishable from a server
    that is down, and the operator needs to know which.
    """
    try:
        if server.transport == "http":
            return _check_http(server)
        return _check_stdio(server)
    except Exception as exc:  # noqa: BLE001 - the check itself must not fail the caller
        logger.warning("Health check for %s raised", server.name, exc_info=True)
        return HealthResult(ok=False, latency_ms=0, error=f"Check failed: {exc}")


def record_health(session: Session, server: McpServer, result: HealthResult) -> McpServer:
    """Store what a check found.

    `updated_at` is deliberately untouched: a check observes the server, it does
    not change how it is configured, and bumping that would make every check
    look like an edit in the audit trail.

    Lives here rather than in the registry so the dependency runs one way —
    health reads the registry, never the reverse.
    """
    server.last_checked_at = datetime.now(timezone.utc).isoformat()
    server.last_health_ok = result.ok
    server.last_health_latency_ms = result.latency_ms
    server.last_health_error = result.error
    session.add(server)
    session.commit()
    session.refresh(server)
    return server
