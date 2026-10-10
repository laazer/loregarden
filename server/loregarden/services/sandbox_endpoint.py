"""Proof that a sandbox's agents talk to the sandbox, not to whatever answers.

A sandbox runs on a copy of production. The MCP URL its agents are handed is
the one thing that can still lead back to production: main's default, an
`LOREGARDEN_MCP_URL` inherited from the operator's shell, a stale port. A URL
that merely *looks* right is not enough — another server may be listening there.

So every process carries an id it serves on `/health`, and a sandbox, once it is
up, asks the URL it would hand out to identify itself. Until the answer is this
process's own id, sandbox agent turns refuse to start (`require_this_instance`)
and say why. The check runs once, after boot, on the event loop — never at
resolve time, where a synchronous request to itself from an async handler would
wait on the very loop it is blocking.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from uuid import uuid4

import httpx

logger = logging.getLogger(__name__)

#: This process, as `/health` reports it. New on every boot.
INSTANCE_ID = uuid4().hex

#: How long boot waits for its own endpoint to answer before giving up.
VERIFY_TIMEOUT_SECONDS = 30.0
_RETRY_SECONDS = 0.5
#: Still binding, or not a loregarden server: retried, then reported.
_NOT_YET = (httpx.HTTPError, ValueError)


class SandboxMcpUrlError(RuntimeError):
    """A sandbox would hand its agents an MCP endpoint that is not itself."""


@dataclass
class _Verification:
    #: The base URL that answered as this process; None until one has.
    verified_base: str | None = None
    #: Why verification failed, when it has; None while pending or after success.
    failure: str | None = None


_state = _Verification()


def reset_verification() -> None:
    """Forget the last result — a new app in the same interpreter (tests) re-verifies."""
    _state.verified_base = None
    _state.failure = None


async def verify_this_instance(base_url: str, *, client: httpx.AsyncClient) -> bool:
    """Ask `base_url`/health who it is, until it answers or the budget runs out.

    Records the outcome for `require_this_instance`. A connection refused is
    retried — the server may still be binding — but a wrong id is final: some
    other server is listening on the URL this sandbox's agents would use.
    """
    base = base_url.rstrip("/")
    loop = asyncio.get_running_loop()
    deadline = loop.time() + VERIFY_TIMEOUT_SECONDS
    last_error = "no answer"
    while loop.time() < deadline:
        try:
            response = await client.get(f"{base}/health", timeout=2.0)
            answered = response.json().get("instance_id") if response.is_success else None
        # silent-ok: retried until the deadline, then recorded as the failure
        except _NOT_YET as exc:
            last_error = f"{type(exc).__name__}: {exc}"
            await asyncio.sleep(_RETRY_SECONDS)
            continue
        if answered == INSTANCE_ID:
            _state.verified_base, _state.failure = base, None
            logger.warning("sandbox: agents' MCP endpoint %s/mcp verified as this instance", base)
            return True
        _state.failure = (
            f"{base} answers as a different server (instance {answered or 'unknown'}), "
            "not this sandbox"
        )
        logger.error("sandbox: %s; agent turns are refused", _state.failure)
        return False
    _state.failure = f"{base} did not answer within {VERIFY_TIMEOUT_SECONDS:.0f}s ({last_error})"
    logger.error("sandbox: %s; agent turns are refused", _state.failure)
    return False


def require_this_instance(base_url: str) -> None:
    """Raise unless `base_url` is the one verified as this sandbox."""
    base = base_url.rstrip("/")
    if _state.verified_base == base:
        return
    if _state.failure is not None:
        raise SandboxMcpUrlError(f"Sandbox MCP endpoint check failed: {_state.failure}.")
    raise SandboxMcpUrlError(
        f"Sandbox MCP endpoint {base} is not verified as this instance yet; "
        "retry in a moment, or check the server log."
    )
