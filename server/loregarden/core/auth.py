"""Optional shared-secret authentication for the control plane.

Loregarden is a local-first tool, but its API writes files and spawns agent
processes, so any local process can drive it. When ``LOREGARDEN_API_TOKEN`` is
set, this middleware requires that token on every request (except unauthenticated
health checks and CORS preflight), closing that gap on shared machines. When the
token is empty the middleware is a no-op, preserving the zero-config local flow.
"""

from __future__ import annotations

import hmac
import logging

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.types import ASGIApp

logger = logging.getLogger(__name__)

# Paths reachable without a token even when auth is enabled.
_EXEMPT_PATHS = frozenset({"/health"})


def _extract_token(request: Request) -> str | None:
    header = request.headers.get("authorization")
    if header and header.lower().startswith("bearer "):
        return header[7:].strip()
    token = request.headers.get("x-loregarden-token")
    return token.strip() if token else None


class TokenAuthMiddleware(BaseHTTPMiddleware):
    """Enforce a bearer token when one is configured."""

    def __init__(self, app: ASGIApp, token: str) -> None:
        super().__init__(app)
        self._token = token or ""

    async def dispatch(self, request: Request, call_next):
        if not self._token:
            return await call_next(request)
        # CORS preflight carries no auth header and must be allowed through.
        if request.method == "OPTIONS":
            return await call_next(request)
        if request.url.path in _EXEMPT_PATHS:
            return await call_next(request)

        presented = _extract_token(request)
        if presented is None or not hmac.compare_digest(presented, self._token):
            return JSONResponse(
                {"detail": "Missing or invalid API token"},
                status_code=401,
            )
        return await call_next(request)
