"""Session-key transport for the claude.ai usage endpoint.

The OAuth route (``api.anthropic.com/api/oauth/usage``) requires the
``user:profile`` scope, and on a given machine there may be no credential that
carries it: a ``claude setup-token`` long-lived token is inference-scoped and
answers ``403 OAuth token does not meet scope requirement user:profile``, while
the macOS Keychain item can be readable yet hold empty ``accessToken`` /
``refreshToken`` fields. Either way the usage modal has no live source and falls
back to indefinitely stale cached meters.

This module is the documented backup path: the same usage payload, read from
claude.ai with the browser's own ``sessionKey`` cookie.

The body is returned raw on purpose. ``usage_service`` owns meter construction,
and claude.ai returns the same shape as the OAuth endpoint (``five_hour``,
``seven_day``, ``limits[]`` with ``scope.model.display_name``), so both routes
share a single parser rather than growing a second one.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

import httpx
from loregarden.config import settings

logger = logging.getLogger(__name__)

SESSION_KEY_FILENAME = ".claude-session-key"
SESSION_KEY_ENV = "CLAUDE_SESSION_KEY"
ORG_UUID_ENV = "CLAUDE_ORG_UUID"

CLAUDE_WEB_ORIGIN = "https://claude.ai"
CLAUDE_ORGS_URL = f"{CLAUDE_WEB_ORIGIN}/api/organizations"

# claude.ai sits behind Cloudflare and rejects requests that don't look like the
# web app. Bump this occasionally; a long-stale version raises the odds of being
# challenged.
_CHROME_VERSION = "131.0.0.0"

# Organization lookup is a second round trip, so remember it for the life of the
# process rather than paying it on every poll. Keyed by session key so rotating
# the credential (or switching accounts) re-resolves instead of reusing a uuid
# the new key may not have access to.
_org_uuid_cache: dict[str, str] = {}


class SessionUsageUnavailable(Exception):
    """The session-key route could not produce a usage payload.

    ``message`` is user-facing: it renders in a ~240px slot next to the provider
    title (``.usage-provider-error`` in ``client/src/index.css``), so keep it
    short and actionable.
    """

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


def session_key_file_path() -> Path:
    """Where a claude.ai ``sessionKey`` cookie can be cached locally.

    Sits under the repo-root ``data/`` directory, which ``.gitignore`` already
    excludes wholesale, alongside the OAuth token file.
    """
    return settings.repo_root / "data" / SESSION_KEY_FILENAME


def read_session_key() -> str | None:
    """Return the configured session key, or None when the route isn't set up.

    Env var wins so a shell can override the file without editing it.
    """
    env_value = os.environ.get(SESSION_KEY_ENV, "").strip()
    if env_value:
        return _validated_session_key(env_value, source=SESSION_KEY_ENV)

    path = session_key_file_path()
    if not path.is_file():
        return None
    try:
        raw = path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        logger.debug("could not read claude session key file %s: %s", path, exc)
        return None
    if not raw:
        return None
    return _validated_session_key(raw, source=str(path))


def _validated_session_key(value: str, *, source: str) -> str | None:
    """Reject anything that isn't a bare cookie value.

    A cookie goes into a header verbatim, so embedded whitespace or non-ASCII
    (pasted terminal output, a whole ``Cookie:`` line, a browser devtools row)
    would otherwise blow up header encoding at request time. Same guard the
    OAuth token file uses, for the same reason.
    """
    if not value.isascii() or any(ch.isspace() for ch in value):
        logger.warning(
            "claude session key from %s doesn't look like a bare cookie value "
            "(non-ASCII or whitespace found) — ignoring it. Copy only the "
            "sessionKey cookie value, without the 'sessionKey=' prefix.",
            source,
        )
        return None
    return value


def _browser_headers(session_key: str) -> dict[str, str]:
    """Headers matching what the claude.ai web app sends.

    Cloudflare inspects the ``sec-fetch-*`` triple, ``origin``/``referer`` and
    the user agent; a bare request with only the cookie gets challenged.
    """
    return {
        "accept": "*/*",
        "accept-language": "en-US,en;q=0.9",
        "content-type": "application/json",
        "anthropic-client-platform": "web_claude_ai",
        "user-agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
            f"(KHTML, like Gecko) Chrome/{_CHROME_VERSION} Safari/537.36"
        ),
        "origin": CLAUDE_WEB_ORIGIN,
        "referer": f"{CLAUDE_WEB_ORIGIN}/settings/usage",
        "sec-fetch-dest": "empty",
        "sec-fetch-mode": "cors",
        "sec-fetch-site": "same-origin",
        "Cookie": f"sessionKey={session_key}",
    }


def _decoded_body(response: httpx.Response) -> Any:
    """Parse a claude.ai response, distinguishing a challenge from an API error.

    A Cloudflare interstitial arrives as an HTML 403, which is a different
    problem from a rejected cookie and needs a different fix, so it must not be
    reported as "session expired".
    """
    content_type = str(response.headers.get("content-type", "")).lower()
    body_head = response.text[:512].lstrip()
    if "text/html" in content_type or body_head.lower().startswith(("<!doctype html", "<html")):
        raise SessionUsageUnavailable("claude.ai blocked the request (Cloudflare challenge).")
    try:
        return response.json()
    except ValueError as exc:
        raise SessionUsageUnavailable("claude.ai returned an unreadable response.") from exc


def _raise_for_status(response: httpx.Response) -> None:
    if response.status_code < 400:
        return
    # HTML bodies are a challenge, not an auth failure — classify those first.
    _decoded_body(response)
    if response.status_code in (401, 403):
        raise SessionUsageUnavailable(
            "Claude session key rejected or expired — save a fresh one (see AGENTS.md)."
        )
    if response.status_code == 429:
        raise SessionUsageUnavailable("claude.ai usage API rate limited — try again shortly.")
    raise SessionUsageUnavailable(f"claude.ai usage request failed (HTTP {response.status_code}).")


def resolve_org_uuid(client: httpx.Client, session_key: str) -> str:
    """Find the organization whose usage to read.

    An explicit override wins, then the per-process cache, then a lookup. When
    the account has several organizations the first chat-capable one matches
    what the web app shows by default.
    """
    override = os.environ.get(ORG_UUID_ENV, "").strip()
    if override:
        return override

    cached = _org_uuid_cache.get(session_key)
    if cached:
        return cached

    response = client.get(CLAUDE_ORGS_URL, headers=_browser_headers(session_key), timeout=15)
    _raise_for_status(response)
    body = _decoded_body(response)
    if not isinstance(body, list):
        raise SessionUsageUnavailable("claude.ai returned no organizations.")

    orgs = [entry for entry in body if isinstance(entry, dict) and entry.get("uuid")]
    if not orgs:
        raise SessionUsageUnavailable("claude.ai returned no organizations.")

    chosen = next(
        (org for org in orgs if "chat" in (org.get("capabilities") or [])),
        orgs[0],
    )
    uuid = str(chosen["uuid"])
    _org_uuid_cache[session_key] = uuid
    return uuid


def fetch_usage_body(client: httpx.Client) -> dict[str, Any] | None:
    """Return the raw claude.ai usage payload, or None when unconfigured.

    Raises ``SessionUsageUnavailable`` when a session key *is* configured but
    can't be used — the caller surfaces that message, since a user who opted
    into this route needs to know why it isn't working.
    """
    session_key = read_session_key()
    if not session_key:
        return None

    org_uuid = resolve_org_uuid(client, session_key)
    response = client.get(
        f"{CLAUDE_ORGS_URL}/{org_uuid}/usage",
        headers=_browser_headers(session_key),
        timeout=15,
    )
    _raise_for_status(response)
    body = _decoded_body(response)
    if not isinstance(body, dict):
        raise SessionUsageUnavailable("claude.ai returned an unexpected usage payload.")
    # A stale cookie can 200 with an error envelope instead of a 401.
    error = body.get("error")
    if isinstance(error, dict):
        raise SessionUsageUnavailable(
            "Claude session key rejected or expired — save a fresh one (see AGENTS.md)."
        )
    return body


def clear_org_cache() -> None:
    """Drop the resolved-organization cache (used by tests)."""
    _org_uuid_cache.clear()
