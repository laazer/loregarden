"""Assemble a ``ProviderUsage`` for Codex.

Meters prefer the live ChatGPT ``/codex/usage`` endpoint; rollout transcripts
are the fallback and always supply the per-model activity breakdown. Lives in
its own module so ``usage_service`` stays under the size gate.

Imports only the public dataclasses from ``usage_service`` — helper logic that
would otherwise pull private symbols is duplicated here on purpose.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Literal

import httpx
from loregarden.services import codex_usage

logger = logging.getLogger(__name__)

WARNING_PERCENT = 75.0
CRITICAL_PERCENT = 90.0
RATE_LIMIT_MAX_BACKOFF_SECONDS = 4 * 60 * 60
MeterStatus = Literal["ok", "warning", "critical"]


def _meter_status(percent_used: float) -> MeterStatus:
    if percent_used >= CRITICAL_PERCENT:
        return "critical"
    if percent_used >= WARNING_PERCENT:
        return "warning"
    return "ok"


def _iso_from_epoch(value: Any) -> str | None:
    number = codex_usage._as_number(value)
    if number is None:
        return None
    seconds = number / 1000 if abs(number) >= 1e10 else number
    return datetime.fromtimestamp(seconds, tz=timezone.utc).isoformat()


def _iso_from_text(value: Any) -> str | None:
    if not isinstance(value, str) or not value.strip():  # py-org: allow-isinstance
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).isoformat()
    except ValueError:
        return None


def _parse_iso(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():  # py-org: allow-isinstance
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return (
        parsed.replace(tzinfo=timezone.utc)
        if parsed.tzinfo is None
        else parsed.astimezone(timezone.utc)
    )


def _active_backoff(cache_entry: dict[str, Any] | None) -> str | None:
    if not cache_entry:
        return None
    until_iso = cache_entry.get("rate_limited_until")
    until = _parse_iso(until_iso)
    if until is None or until <= datetime.now(tz=timezone.utc):
        return None
    return until_iso if isinstance(until_iso, str) else None  # py-org: allow-isinstance


def _backoff_until(response: httpx.Response, streak: int) -> str:
    raw = response.headers.get("retry-after", "").strip()
    try:
        base = max(1, int(raw)) if raw else 300
    except ValueError:
        base = 300
    seconds = min(base * (2 ** max(streak, 0)), RATE_LIMIT_MAX_BACKOFF_SECONDS)
    return (datetime.now(tz=timezone.utc) + timedelta(seconds=seconds)).isoformat()


def _http_error(response: httpx.Response) -> str:
    if response.status_code == 401:
        return "Codex session expired — run `codex login` to re-authenticate."
    if response.status_code == 429:
        return "Codex usage API rate limited — backing off."
    return f"Codex usage request failed (HTTP {response.status_code})."


def _scan_activity(days_back: int = 7):
    from loregarden.services.usage_service import UsageBreakdownItem

    totals = codex_usage.model_token_totals(days_back)
    grand_total = sum(totals.values())
    if not grand_total:
        return []
    items = [
        UsageBreakdownItem(
            name=name,
            amount=amount,
            unit="tokens",
            share_percent=amount / grand_total * 100,
        )
        for name, amount in totals.items()
    ]
    items.sort(key=lambda item: item.amount, reverse=True)
    return items[:8]


def _append_window(meters: list, key: str, window: Any) -> None:
    from loregarden.services.usage_service import UsageMeter

    if not isinstance(window, dict):  # py-org: allow-isinstance — foreign rate_limits JSON
        return
    used = codex_usage._as_number(window.get("used_percent"))
    if used is None:
        return
    meters.append(
        UsageMeter(
            key=key,
            label=codex_usage.window_label(window.get("window_minutes")),
            used=used,
            limit=100.0,
            unit="percent",
            percent_used=used,
            resets_at=_iso_from_epoch(window.get("resets_at")),
            status=_meter_status(used),
        )
    )


def _meters_from_limits(limits: dict[str, Any]) -> list:
    meters: list = []
    _append_window(meters, "primary", limits.get("primary"))
    _append_window(meters, "secondary", limits.get("secondary"))
    return meters


def _from_local_transcripts(*, error: str | None = None):
    from loregarden.services.usage_service import ProviderUsage

    limits, observed_at = codex_usage.latest_rate_limits()
    breakdown = _scan_activity()
    if limits is None:
        return ProviderUsage(
            provider="codex",
            logged_in=True,
            error=error or "No usage recorded yet — Codex writes limits on its first run.",
            breakdown=breakdown,
        )
    return ProviderUsage(
        provider="codex",
        plan=codex_usage.plan_label(limits),
        logged_in=True,
        error=error,
        meters=_meters_from_limits(limits),
        breakdown=breakdown,
        observed_at=_iso_from_text(observed_at),
    )


def fetch_codex_provider(
    client: httpx.Client,
    cache_entry: dict[str, Any] | None = None,
):
    """Codex usage: live ChatGPT meters, local activity breakdown."""
    from loregarden.services.usage_service import ProviderUsage

    breakdown = _scan_activity()
    if not codex_usage.is_signed_in():
        return ProviderUsage(
            provider="codex",
            logged_in=False,
            error="Not logged in. Run `codex login` to authenticate.",
            breakdown=breakdown,
        )

    backoff_until = _active_backoff(cache_entry)
    if backoff_until:
        raw_plan = cache_entry.get("plan") if cache_entry else None
        plan_name = raw_plan if isinstance(raw_plan, str) else None  # py-org: allow-isinstance
        until = _parse_iso(backoff_until)
        minutes = 1
        if until is not None:
            minutes = max(
                1,
                (int((until - datetime.now(tz=timezone.utc)).total_seconds()) + 59) // 60,
            )
        return ProviderUsage(
            provider="codex",
            plan=plan_name,
            logged_in=True,
            error=f"Codex usage API rate limited — backing off (~{minutes} min remaining).",
            breakdown=breakdown,
            rate_limited_until=backoff_until,
        )

    try:
        limits, response = codex_usage.fetch_live_rate_limits(client)
    except httpx.HTTPError as exc:
        logger.debug("codex usage API request failed: %s", exc)
        return _from_local_transcripts(error=f"Codex usage API unreachable ({type(exc).__name__}).")

    if response is None:
        return _from_local_transcripts()

    if response.status_code >= 400:
        rate_limited_until = None
        rate_limit_streak = None
        if response.status_code == 429:
            streak_raw = (cache_entry or {}).get("rate_limit_streak", 0)
            prior = 0
            if isinstance(streak_raw, int) and streak_raw >= 0:  # py-org: allow-isinstance
                prior = streak_raw
            rate_limit_streak = prior + 1
            rate_limited_until = _backoff_until(response, prior)
        fallback = _from_local_transcripts(error=_http_error(response))
        fallback.rate_limited_until = rate_limited_until
        fallback.rate_limit_streak = rate_limit_streak
        return fallback

    if limits is None:
        return _from_local_transcripts(error="Codex usage API returned no rate-limit windows.")

    return ProviderUsage(
        provider="codex",
        plan=codex_usage.plan_label(limits),
        logged_in=True,
        meters=_meters_from_limits(limits),
        breakdown=breakdown,
    )
