"""Rate-limit backoff for the provider usage endpoints.

A provider that answers 429 is not asked again until its backoff runs out, and
the backoff doubles with each consecutive 429 up to a four-hour cap. The state
lives in the usage cache entry, keyed to the credential that earned it, so a
fresh login is tried at once instead of inheriting another credential's penalty.
"""

from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, ValidationError

logger = logging.getLogger(__name__)

RATE_LIMIT_MAX_BACKOFF_SECONDS = 4 * 60 * 60  # cap consecutive-rate-limit backoff at 4 hours

PROVIDER_LABELS = {"claude": "Claude", "cursor": "Cursor", "codex": "Codex"}


class RateLimitState(BaseModel):
    """The backoff fields of a usage cache entry."""

    model_config = ConfigDict(extra="ignore")

    rate_limited_until: AwareDatetime | None = None
    rate_limit_streak: int = Field(default=0, ge=0)
    rate_limited_credential: str | None = None

    def active_until(self) -> datetime | None:
        """When the backoff ends, or None when there is none still running."""
        until = self.rate_limited_until
        if until is None or until <= datetime.now(tz=timezone.utc):
            return None
        return until


def rate_limit_state(
    cache_entry: dict[str, Any] | None, credential: str | None = None
) -> RateLimitState:
    """The cached backoff, read for ``credential`` when the provider has one.

    A backoff another credential earned — or one recorded before credentials
    were — does not apply: retrying costs one request, while honouring it is
    what kept a fresh login showing an old setup token's backoff for hours.
    """
    try:
        state = RateLimitState.model_validate(cache_entry or {})
    except ValidationError as exc:
        logger.warning(
            "usage cache backoff state is malformed; ignoring it until the next "
            "poll rewrites it: %s",
            exc,
        )
        return RateLimitState()
    if credential is not None and state.rate_limited_credential != credential:
        return RateLimitState()
    return state


def credential_fingerprint(oauth: dict[str, Any]) -> str:
    """A stable, non-reversible id for a credential, safe to write to the usage cache.

    Taken from the credential as stored, before any in-memory refresh, so it only
    changes when the login itself does (a re-login, or the CLI rotating it).
    """
    secret = str(oauth.get("refreshToken") or oauth.get("accessToken") or "")
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()[:16]


def retry_after_seconds(response: httpx.Response) -> int | None:
    raw = response.headers.get("retry-after", "").strip()
    if not raw:
        return None
    try:
        return max(1, int(raw))
    except ValueError:
        return None


def rate_limit_backoff_seconds(
    response: httpx.Response, streak: int, *, default_seconds: int = 300
) -> int:
    base = retry_after_seconds(response) or default_seconds
    escalated = base * (2 ** max(streak, 0))
    return min(escalated, RATE_LIMIT_MAX_BACKOFF_SECONDS)


def rate_limit_until(response: httpx.Response, streak: int, *, default_seconds: int = 300) -> str:
    seconds = rate_limit_backoff_seconds(response, streak, default_seconds=default_seconds)
    return (datetime.now(tz=timezone.utc) + timedelta(seconds=seconds)).isoformat()


def provider_label(provider: str) -> str:
    return PROVIDER_LABELS.get(provider, provider.title())


def rate_limit_backoff_error(provider: str, until: datetime) -> str:
    remaining = max(0, int((until - datetime.now(tz=timezone.utc)).total_seconds()))
    minutes = max(1, (remaining + 59) // 60)
    return (
        f"{provider_label(provider)} usage API rate limited — "
        f"backing off (~{minutes} min remaining)."
    )
