"""Send one endpoint check from the review page to a server on this machine.

The browser cannot call another local server directly (CORS), so the review
page posts the request here and this sends it. Loopback only, no redirects:
the base URL comes from the page and the path from an agent's review kit, and
neither may turn this into a way to reach anything off the machine.
"""

from __future__ import annotations

import time
from urllib.parse import urlsplit

import httpx
from loregarden.services.review_kit import HttpMethod
from pydantic import BaseModel, ConfigDict, field_validator

LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
TIMEOUT_SECONDS = 30.0
MAX_BODY_CHARS = 64 * 1024


class TryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    base_url: str
    method: HttpMethod = HttpMethod.GET
    path: str
    body: str = ""

    @field_validator("base_url")
    @classmethod
    def _loopback(cls, value: str) -> str:
        parts = urlsplit(value)
        if parts.scheme not in {"http", "https"} or parts.hostname not in LOOPBACK_HOSTS:
            raise ValueError("base_url must be an http(s) URL on 127.0.0.1, localhost or ::1")
        if parts.path not in {"", "/"} or parts.query or parts.fragment or parts.username:
            raise ValueError("base_url must be an origin only, like http://127.0.0.1:8000")
        return f"{parts.scheme}://{parts.netloc}"

    @field_validator("path")
    @classmethod
    def _relative(cls, value: str) -> str:
        if not value.startswith("/") or value.startswith("//"):
            raise ValueError("path must start with a single '/'")
        return value


class TryResult(BaseModel):
    ok: bool
    status: int | None = None
    elapsed_ms: int
    content_type: str = ""
    body: str = ""
    truncated: bool = False
    error: str = ""


def send_try_request(request: TryRequest, *, client: httpx.Client | None = None) -> TryResult:
    """Send it and report what came back; a transport failure is a result, not a raise."""
    started = time.monotonic()
    headers = {"Content-Type": "application/json"} if request.body else {}
    owned = client is None
    http = client or httpx.Client(timeout=TIMEOUT_SECONDS, follow_redirects=False)
    try:
        response = http.request(
            request.method.value,
            f"{request.base_url}{request.path}",
            content=request.body.encode("utf-8") if request.body else None,
            headers=headers,
        )
    except httpx.HTTPError as exc:
        return TryResult(
            ok=False,
            elapsed_ms=int((time.monotonic() - started) * 1000),
            error=f"{type(exc).__name__}: {exc}",
        )
    finally:
        if owned:
            http.close()
    text = response.text
    return TryResult(
        ok=True,
        status=response.status_code,
        elapsed_ms=int((time.monotonic() - started) * 1000),
        content_type=response.headers.get("content-type", ""),
        body=text[:MAX_BODY_CHARS],
        truncated=len(text) > MAX_BODY_CHARS,
    )
