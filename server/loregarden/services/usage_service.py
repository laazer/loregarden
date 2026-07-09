from __future__ import annotations

import json
import logging
import os
import platform
import re
import sqlite3
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import httpx
from loregarden.config import settings

logger = logging.getLogger(__name__)

USAGE_CACHE_FILENAME = "usage-cache.json"

WARNING_PERCENT = 80.0
CRITICAL_PERCENT = 90.0

CLAUDE_CLIENT_ID = "9d1c250a-e61b-44d9-88ed-5944d1962f5e"
CLAUDE_SCOPES = (
    "user:profile user:inference user:sessions:claude_code user:mcp_servers user:file_upload"
)
CLAUDE_USAGE_URL = "https://api.anthropic.com/api/oauth/usage"
CLAUDE_REFRESH_URL = "https://platform.claude.com/v1/oauth/token"

CURSOR_USAGE_URL = "https://api2.cursor.sh/aiserver.v1.DashboardService/GetCurrentPeriodUsage"
CURSOR_PLAN_URL = "https://api2.cursor.sh/aiserver.v1.DashboardService/GetPlanInfo"
CURSOR_ACCESS_KEY = "cursorAuth/accessToken"


@dataclass
class UsageBreakdownItem:
    name: str
    amount: float
    unit: str
    share_percent: float

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "amount": round(self.amount, 2),
            "unit": self.unit,
            "share_percent": round(self.share_percent, 1),
        }


@dataclass
class UsageMeter:
    key: str
    label: str
    used: float
    limit: float | None
    unit: str
    percent_used: float | None = None
    resets_at: str | None = None
    status: str = "ok"

    def as_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "label": self.label,
            "used": round(self.used, 2),
            "limit": round(self.limit, 2) if self.limit is not None else None,
            "unit": self.unit,
            "percent_used": round(self.percent_used, 1) if self.percent_used is not None else None,
            "resets_at": self.resets_at,
            "status": self.status,
        }


@dataclass
class ProviderUsage:
    provider: str
    plan: str | None = None
    logged_in: bool = False
    error: str | None = None
    meters: list[UsageMeter] = field(default_factory=list)
    breakdown: list[UsageBreakdownItem] = field(default_factory=list)
    from_cache: bool = False
    cached_at: str | None = None
    rate_limited_until: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "plan": self.plan,
            "logged_in": self.logged_in,
            "error": self.error,
            "meters": [m.as_dict() for m in self.meters],
            "breakdown": [b.as_dict() for b in self.breakdown],
            "from_cache": self.from_cache,
            "cached_at": self.cached_at,
        }


def _meter_status(percent_used: float | None) -> str:
    if percent_used is None:
        return "ok"
    if percent_used >= CRITICAL_PERCENT:
        return "critical"
    if percent_used >= WARNING_PERCENT:
        return "warning"
    return "ok"


def _iso_from_epoch_ms(value: Any) -> str | None:
    number = _as_number(value)
    if number is None:
        return None
    seconds = number / 1000 if abs(number) >= 1e10 else number
    return datetime.fromtimestamp(seconds, tz=timezone.utc).isoformat()


def _iso_from_text(value: Any) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).isoformat()
    except ValueError:
        return None


def _as_number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    return None


def _cents_to_dollars(value: float) -> float:
    return value / 100.0


def _claude_home() -> Path:
    override = os.environ.get("CLAUDE_CONFIG_DIR", "").strip()
    return Path(override).expanduser() if override else Path.home() / ".claude"


def _read_claude_credentials_file() -> dict[str, Any] | None:
    path = _claude_home() / ".credentials.json"
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.debug("could not read claude credentials file %s: %s", path, exc)
        return None


def claude_oauth_token_file_path() -> Path:
    """Where a `claude setup-token` long-lived token can be cached locally.

    Loaded automatically on every run, so once saved here the token survives
    server restarts without needing to be exported into the shell each time.
    Gitignored via the repo-root ``data/`` rule.
    """
    return settings.repo_root / "data" / ".claude-oauth-token"


def _read_claude_oauth_token_file() -> str | None:
    path = claude_oauth_token_file_path()
    if not path.is_file():
        return None
    try:
        token = path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        logger.debug("could not read claude oauth token file %s: %s", path, exc)
        return None
    if not token:
        return None
    # A bearer token is a single ASCII run with no embedded whitespace. If the
    # file instead holds captured terminal output (e.g. `claude setup-token`'s
    # interactive UI redirected wholesale into the file — spinner frames,
    # prompts, line breaks), using it as-is crashes the HTTP client when it
    # tries to encode the Authorization header. Treat anything that doesn't
    # look like a bare token as absent rather than letting it blow up the request.
    if not token.isascii() or any(ch.isspace() for ch in token):
        logger.warning(
            "claude oauth token file %s doesn't look like a bare token "
            "(non-ASCII or whitespace found) — ignoring it. Regenerate with "
            "`claude setup-token`, copying only the printed token into the file.",
            path,
        )
        return None
    return token


def _read_claude_keychain_credentials() -> dict[str, Any] | None:
    if platform.system() != "Darwin":
        return None
    try:
        result = subprocess.run(
            ["security", "find-generic-password", "-s", "Claude Code-credentials", "-w"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        if result.returncode != 0 or not result.stdout.strip():
            return None
        return json.loads(result.stdout)
    except (OSError, json.JSONDecodeError, subprocess.SubprocessError) as exc:
        logger.debug("could not read claude keychain credentials: %s", exc)
        return None


def _claude_keychain_item_exists() -> bool:
    """Check whether the Keychain item exists, without reading its value.

    Deliberately omits ``-w`` (or ``-g``) so the credential itself is never
    captured — this only inspects the item's presence via the exit code.
    """
    if platform.system() != "Darwin":
        return False
    try:
        result = subprocess.run(
            ["security", "find-generic-password", "-s", "Claude Code-credentials"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        return result.returncode == 0
    except (OSError, subprocess.SubprocessError) as exc:
        logger.debug("could not check claude keychain item existence: %s", exc)
        return False


def _claude_oauth() -> dict[str, Any] | None:
    env_token = (
        os.environ.get("CLAUDE_CODE_OAUTH_TOKEN", "").strip()
        or _read_claude_oauth_token_file()
        or ""
    )
    file_data = _read_claude_credentials_file()
    keychain_data = _read_claude_keychain_credentials()
    for data in (keychain_data, file_data):
        oauth = (data or {}).get("claudeAiOauth") or {}
        if oauth.get("accessToken"):
            if env_token:
                oauth = {**oauth, "accessToken": env_token}
            return oauth
    if env_token:
        return {"accessToken": env_token}
    return None


def _claude_login_diagnosis() -> str:
    """Error message for the no-usable-oauth case.

    A missing credentials file plus a present-but-unreadable Keychain item
    means the user is actually logged in but the backend process was denied
    read access. macOS Keychain ACLs require GUI interaction to release a
    password's value to a process by default; a backgrounded process like
    this server gets a silent denial instead of a prompt
    (https://github.com/anthropics/claude-code/issues/9403,
    https://github.com/anthropics/claude-code/issues/44089). Re-running
    `claude` won't fix that — the documented workaround is a long-lived
    token (https://code.claude.com/docs/en/authentication#generate-a-long-lived-token).
    """
    if _claude_keychain_item_exists():
        return "Keychain unreadable by this process (not a login issue) — run `task claude:setup-token`."
    return "Not logged in. Run `claude` to authenticate."


def _claude_plan_label(oauth: dict[str, Any]) -> str | None:
    subscription = str(oauth.get("subscriptionType") or "").strip()
    if not subscription:
        return None
    tier = str(oauth.get("rateLimitTier") or "").strip()
    label = subscription.replace("_", " ").title()
    if tier:
        match = re.search(r"(\d+x)", tier, re.I)
        if match:
            label = f"{label} {match.group(1)}"
    return label


def _refresh_claude_token(oauth: dict[str, Any], client: httpx.Client) -> dict[str, Any]:
    refresh_token = str(oauth.get("refreshToken") or "").strip()
    if not refresh_token:
        return oauth
    response = client.post(
        CLAUDE_REFRESH_URL,
        json={
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": CLAUDE_CLIENT_ID,
            "scope": CLAUDE_SCOPES,
        },
        timeout=15,
    )
    if response.status_code >= 400:
        return oauth
    payload = response.json()
    access = str(payload.get("access_token") or "").strip()
    if not access:
        return oauth
    updated = {**oauth, "accessToken": access}
    if payload.get("refresh_token"):
        updated["refreshToken"] = payload["refresh_token"]
    if payload.get("expires_in") is not None:
        updated["expiresAt"] = (
            datetime.now(tz=timezone.utc).timestamp() * 1000 + float(payload["expires_in"]) * 1000
        )
    return updated


def _append_claude_window(
    body: dict[str, Any],
    key: str,
    label: str,
    meters: list[UsageMeter],
) -> None:
    window = body.get(key)
    if not isinstance(window, dict):
        return
    used = _as_number(window.get("utilization"))
    if used is None:
        return
    meters.append(
        UsageMeter(
            key=key,
            label=label,
            used=used,
            limit=100.0,
            unit="percent",
            percent_used=used,
            resets_at=_iso_from_text(window.get("resets_at"))
            or _iso_from_epoch_ms(window.get("resets_at")),
            status=_meter_status(used),
        )
    )


def _append_claude_scoped_limit(
    limits: Any,
    model_name: str,
    label: str,
    meters: list[UsageMeter],
) -> None:
    if not isinstance(limits, list):
        return
    for entry in limits:
        if not isinstance(entry, dict):
            continue
        scope = entry.get("scope") if isinstance(entry.get("scope"), dict) else {}
        model = scope.get("model") if isinstance(scope.get("model"), dict) else {}
        if entry.get("kind") != "weekly_scoped":
            continue
        if model.get("display_name") != model_name:
            continue
        used = _as_number(entry.get("percent"))
        if used is None:
            continue
        meters.append(
            UsageMeter(
                key=f"scoped_{model_name.lower()}",
                label=label,
                used=used,
                limit=100.0,
                unit="percent",
                percent_used=used,
                resets_at=_iso_from_text(entry.get("resets_at"))
                or _iso_from_epoch_ms(entry.get("resets_at")),
                status=_meter_status(used),
            )
        )
        return


def _append_claude_extra_usage(body: dict[str, Any], meters: list[UsageMeter]) -> None:
    extra = body.get("extra_usage")
    if not isinstance(extra, dict) or extra.get("is_enabled") is not True:
        return
    used_cents = _as_number(extra.get("used_credits"))
    if used_cents is None:
        return
    used = _cents_to_dollars(used_cents)
    limit_cents = _as_number(extra.get("monthly_limit"))
    if limit_cents and limit_cents > 0:
        limit = _cents_to_dollars(limit_cents)
        percent = used / limit * 100 if limit > 0 else None
        meters.append(
            UsageMeter(
                key="extra_usage",
                label="Extra usage",
                used=used,
                limit=limit,
                unit="dollars",
                percent_used=percent,
                status=_meter_status(percent),
            )
        )
    elif used > 0:
        meters.append(
            UsageMeter(
                key="extra_usage",
                label="Extra usage",
                used=used,
                limit=None,
                unit="dollars",
            )
        )


def _scan_claude_logs(days_back: int = 7) -> list[UsageBreakdownItem]:
    roots = [_claude_home() / "projects"]
    since = datetime.now(tz=timezone.utc).timestamp() - days_back * 86400
    totals: dict[str, float] = {}
    for root in roots:
        if not root.is_dir():
            continue
        for path in root.rglob("*.jsonl"):
            try:
                for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
                    if '"usage"' not in line:
                        continue
                    try:
                        row = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    ts = _as_number(row.get("timestamp"))
                    if ts is not None and ts < since:
                        continue
                    usage = row.get("usage")
                    if not isinstance(usage, dict):
                        continue
                    model = (
                        row.get("model") or row.get("message", {}).get("model")
                        if isinstance(row.get("message"), dict)
                        else None
                    )
                    model_name = str(model or "unknown").strip() or "unknown"
                    input_tokens = _as_number(usage.get("input_tokens")) or 0
                    output_tokens = _as_number(usage.get("output_tokens")) or 0
                    cache_read = _as_number(usage.get("cache_read_input_tokens")) or 0
                    cache_create = _as_number(usage.get("cache_creation_input_tokens")) or 0
                    tokens = input_tokens + output_tokens + cache_read + cache_create
                    if tokens <= 0:
                        continue
                    totals[model_name] = totals.get(model_name, 0) + tokens
            except OSError:
                continue
    if not totals:
        return []
    grand_total = sum(totals.values())
    items = [
        UsageBreakdownItem(
            name=name,
            amount=amount,
            unit="tokens",
            share_percent=amount / grand_total * 100 if grand_total else 0,
        )
        for name, amount in totals.items()
    ]
    items.sort(key=lambda item: item.amount, reverse=True)
    return items[:8]


def _parse_iso_timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _retry_after_seconds(response: httpx.Response) -> int | None:
    raw = response.headers.get("retry-after", "").strip()
    if not raw:
        return None
    try:
        return max(1, int(raw))
    except ValueError:
        return None


def _rate_limit_until(response: httpx.Response, *, default_seconds: int = 300) -> str:
    seconds = _retry_after_seconds(response) or default_seconds
    return (datetime.now(tz=timezone.utc) + timedelta(seconds=seconds)).isoformat()


def _provider_label(provider: str) -> str:
    return "Claude" if provider == "claude" else "Cursor"


def _format_usage_http_error(
    provider: str, response: httpx.Response, *, has_refresh_token: bool = True
) -> str:
    label = _provider_label(provider)
    if response.status_code == 401:
        if provider == "claude" and not has_refresh_token:
            # A bare access token (CLAUDE_CODE_OAUTH_TOKEN / cached token file)
            # has no refresh token, so it can't silently renew — and re-running
            # `claude` to log in again does nothing for it, since it's a
            # separate credential from the interactive session's OAuth login.
            return "Cached token rejected or expired — run `task claude:setup-token` to refresh it."
        return f"{label} session expired — run `claude` to re-authenticate."
    if response.status_code == 403:
        if provider == "claude" and not has_refresh_token:
            # `claude setup-token` tokens are documented as scoped to inference
            # only — that's enough for Baxter/CLI-adapter calls but apparently
            # not for this usage-limits endpoint, which needs the broader scopes
            # (user:profile etc.) that only an interactive `/login` session gets.
            # Regenerating the token won't change its scope, so don't suggest that.
            return "This token is scoped to inference only — can't read live usage limits (HTTP 403)."
        return f"{label} usage request failed (HTTP {response.status_code})."
    if response.status_code == 429:
        detail = ""
        try:
            body = response.json()
            error = body.get("error") if isinstance(body, dict) else None
            if isinstance(error, dict):
                detail = str(error.get("message") or error.get("type") or "").strip()
        except (json.JSONDecodeError, ValueError):
            detail = ""
        retry = _retry_after_seconds(response)
        parts = [f"{label} usage API rate limited"]
        if detail:
            parts.append(f"({detail})")
        if retry is not None:
            parts.append(f"— retry in ~{retry // 60 or 1} min")
        return " ".join(parts)
    return f"{label} usage request failed (HTTP {response.status_code})."


def _claude_usage_request(client: httpx.Client, access_token: str) -> httpx.Response:
    return client.get(
        CLAUDE_USAGE_URL,
        headers={
            "Authorization": f"Bearer {access_token}",
            "Accept": "application/json",
            "Content-Type": "application/json",
            "anthropic-beta": "oauth-2025-04-20",
            "User-Agent": "loregarden/0.1",
        },
        timeout=10,
    )


def _rate_limit_backoff_error(provider: str, until_iso: str) -> str:
    until = _parse_iso_timestamp(until_iso)
    if until is None:
        return f"{_provider_label(provider)} usage API rate limited — backing off."
    remaining = max(0, int((until - datetime.now(tz=timezone.utc)).total_seconds()))
    minutes = max(1, (remaining + 59) // 60)
    return (
        f"{_provider_label(provider)} usage API rate limited — "
        f"backing off (~{minutes} min remaining)."
    )


def _active_rate_limit_until(cache_entry: dict[str, Any] | None) -> str | None:
    if not cache_entry:
        return None
    until_iso = cache_entry.get("rate_limited_until")
    if not isinstance(until_iso, str):
        return None
    until = _parse_iso_timestamp(until_iso)
    if until is None or until <= datetime.now(tz=timezone.utc):
        return None
    return until_iso


def _fetch_claude_usage(
    client: httpx.Client,
    cache_entry: dict[str, Any] | None = None,
) -> ProviderUsage:
    oauth = _claude_oauth()
    if not oauth or not str(oauth.get("accessToken") or "").strip():
        return ProviderUsage(
            provider="claude",
            logged_in=False,
            error=_claude_login_diagnosis(),
        )

    scopes = oauth.get("scopes")
    if isinstance(scopes, list) and scopes and "user:profile" not in scopes:
        return ProviderUsage(
            provider="claude",
            plan=_claude_plan_label(oauth),
            logged_in=True,
            error="Re-login with `claude` to read live usage limits.",
            breakdown=_scan_claude_logs(),
        )

    access_token = str(oauth.get("accessToken") or "").strip()
    expires_at = _as_number(oauth.get("expiresAt"))
    if (
        expires_at is not None
        and expires_at <= datetime.now(tz=timezone.utc).timestamp() * 1000 + 300_000
    ):
        oauth = _refresh_claude_token(oauth, client)
        access_token = str(oauth.get("accessToken") or "").strip()

    backoff_until = _active_rate_limit_until(cache_entry)
    if backoff_until:
        return ProviderUsage(
            provider="claude",
            plan=_claude_plan_label(oauth),
            logged_in=True,
            error=_rate_limit_backoff_error("claude", backoff_until),
            breakdown=_scan_claude_logs(),
            rate_limited_until=backoff_until,
        )

    response = _claude_usage_request(client, access_token)
    if response.status_code == 401 and str(oauth.get("refreshToken") or "").strip():
        refreshed = _refresh_claude_token(oauth, client)
        refreshed_token = str(refreshed.get("accessToken") or "").strip()
        if refreshed_token and refreshed_token != access_token:
            oauth = refreshed
            access_token = refreshed_token
            response = _claude_usage_request(client, access_token)

    if response.status_code >= 400:
        rate_limited_until = (
            _rate_limit_until(response) if response.status_code == 429 else None
        )
        return ProviderUsage(
            provider="claude",
            plan=_claude_plan_label(oauth),
            logged_in=True,
            error=_format_usage_http_error(
                "claude", response, has_refresh_token=bool(str(oauth.get("refreshToken") or "").strip())
            ),
            breakdown=_scan_claude_logs(),
            rate_limited_until=rate_limited_until,
        )

    body = response.json()
    meters: list[UsageMeter] = []
    _append_claude_window(body, "five_hour", "Session (5h)", meters)
    _append_claude_window(body, "seven_day", "Weekly", meters)
    _append_claude_window(body, "seven_day_sonnet", "Sonnet weekly", meters)
    _append_claude_scoped_limit(body.get("limits"), "Fable", "Fable weekly", meters)
    _append_claude_extra_usage(body, meters)

    return ProviderUsage(
        provider="claude",
        plan=_claude_plan_label(oauth),
        logged_in=True,
        meters=meters,
        breakdown=_scan_claude_logs(),
    )


def _cursor_state_db() -> Path:
    home = Path.home()
    if platform.system() == "Darwin":
        return home / "Library/Application Support/Cursor/User/globalStorage/state.vscdb"
    if platform.system() == "Windows":
        appdata = os.environ.get("APPDATA", "")
        return Path(appdata) / "Cursor/User/globalStorage/state.vscdb"
    return home / ".config/Cursor/User/globalStorage/state.vscdb"


def _read_cursor_access_token() -> str | None:
    db_path = _cursor_state_db()
    if not db_path.is_file():
        return None
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        try:
            row = conn.execute(
                "SELECT value FROM ItemTable WHERE key = ? LIMIT 1",
                (CURSOR_ACCESS_KEY,),
            ).fetchone()
        finally:
            conn.close()
        if not row or not row[0]:
            return None
        raw = row[0]
        if isinstance(raw, bytes):
            return raw.decode("utf-8") or None
        return str(raw) or None
    except sqlite3.Error as exc:
        logger.debug("cursor credential sqlite read failed: %s", exc)
        return None


def _cursor_connect_post(client: httpx.Client, url: str, token: str) -> httpx.Response:
    return client.post(
        url,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Connect-Protocol-Version": "1",
            "User-Agent": "loregarden/0.1",
        },
        json={},
        timeout=15,
    )


def _append_cursor_percent_meter(
    meters: list[UsageMeter],
    key: str,
    label: str,
    value: Any,
    resets_at: str | None,
) -> None:
    used = _as_number(value)
    if used is None:
        return
    meters.append(
        UsageMeter(
            key=key,
            label=label,
            used=used,
            limit=100.0,
            unit="percent",
            percent_used=used,
            resets_at=resets_at,
            status=_meter_status(used),
        )
    )


def _append_cursor_dollar_meter(
    meters: list[UsageMeter],
    key: str,
    label: str,
    used_cents: float,
    limit_cents: float,
    resets_at: str | None,
) -> None:
    used = _cents_to_dollars(used_cents)
    limit = _cents_to_dollars(limit_cents)
    percent = used / limit * 100 if limit > 0 else None
    meters.append(
        UsageMeter(
            key=key,
            label=label,
            used=used,
            limit=limit,
            unit="dollars",
            percent_used=percent,
            resets_at=resets_at,
            status=_meter_status(percent),
        )
    )


def _scan_cursor_activity(days_back: int = 7) -> list[UsageBreakdownItem]:
    db_path = Path.home() / ".cursor/ai-tracking/ai-code-tracking.db"
    if not db_path.is_file():
        return []
    since_ms = int((datetime.now(tz=timezone.utc).timestamp() - days_back * 86400) * 1000)
    totals: dict[str, int] = {}
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        try:
            rows = conn.execute(
                """
                SELECT COALESCE(NULLIF(model, ''), NULLIF(source, ''), 'unknown') AS name,
                       COUNT(*) AS events
                FROM ai_code_hashes
                WHERE createdAt >= ?
                GROUP BY name
                ORDER BY events DESC
                LIMIT 12
                """,
                (since_ms,),
            ).fetchall()
        finally:
            conn.close()
    except sqlite3.Error as exc:
        logger.debug("cursor usage sqlite read failed: %s", exc)
        return []
    for name, count in rows:
        totals[str(name)] = int(count)
    if not totals:
        return []
    grand_total = sum(totals.values())
    return [
        UsageBreakdownItem(
            name=name,
            amount=float(count),
            unit="events",
            share_percent=count / grand_total * 100 if grand_total else 0,
        )
        for name, count in sorted(totals.items(), key=lambda item: item[1], reverse=True)
    ][:8]


def _fetch_cursor_usage(
    client: httpx.Client,
    cache_entry: dict[str, Any] | None = None,
) -> ProviderUsage:
    token = _read_cursor_access_token()
    if not token:
        return ProviderUsage(
            provider="cursor",
            logged_in=False,
            error="Not logged in to Cursor.",
        )

    backoff_until = _active_rate_limit_until(cache_entry)
    if backoff_until:
        plan_name = cache_entry.get("plan") if cache_entry else None
        return ProviderUsage(
            provider="cursor",
            plan=plan_name if isinstance(plan_name, str) else None,
            logged_in=True,
            error=_rate_limit_backoff_error("cursor", backoff_until),
            breakdown=_scan_cursor_activity(),
            rate_limited_until=backoff_until,
        )

    usage_response = _cursor_connect_post(client, CURSOR_USAGE_URL, token)
    plan_response = _cursor_connect_post(client, CURSOR_PLAN_URL, token)
    if usage_response.status_code >= 400:
        rate_limited_until = (
            _rate_limit_until(usage_response) if usage_response.status_code == 429 else None
        )
        return ProviderUsage(
            provider="cursor",
            logged_in=True,
            error=_format_usage_http_error("cursor", usage_response),
            breakdown=_scan_cursor_activity(),
            rate_limited_until=rate_limited_until,
        )

    usage_body = usage_response.json()
    plan_body = plan_response.json() if plan_response.status_code < 400 else {}
    plan_name = None
    if isinstance(plan_body, dict):
        plan_name = (
            str(
                plan_body.get("planName")
                or plan_body.get("plan")
                or (
                    plan_body.get("membershipType")
                    if isinstance(plan_body.get("membershipType"), str)
                    else ""
                )
                or ""
            ).strip()
            or None
        )

    if usage_body.get("enabled") is False:
        return ProviderUsage(
            provider="cursor",
            plan=plan_name,
            logged_in=True,
            error="No active Cursor subscription.",
            breakdown=_scan_cursor_activity(),
        )

    plan_usage = usage_body.get("planUsage")
    if not isinstance(plan_usage, dict):
        return ProviderUsage(
            provider="cursor",
            plan=plan_name,
            logged_in=True,
            error="Cursor usage data unavailable.",
            breakdown=_scan_cursor_activity(),
        )

    cycle_end = _iso_from_epoch_ms(usage_body.get("billingCycleEnd"))
    meters: list[UsageMeter] = []

    limit_cents = _as_number(plan_usage.get("limit"))
    remaining_cents = _as_number(plan_usage.get("remaining"))
    total_spend_cents = _as_number(plan_usage.get("totalSpend"))
    if total_spend_cents is None and limit_cents is not None and remaining_cents is not None:
        total_spend_cents = max(0.0, limit_cents - remaining_cents)

    total_percent = _as_number(plan_usage.get("totalPercentUsed"))
    if total_percent is None and limit_cents and total_spend_cents is not None and limit_cents > 0:
        total_percent = total_spend_cents / limit_cents * 100

    normalized_plan = (plan_name or "").lower()
    spend_limit = usage_body.get("spendLimitUsage")
    pooled_limit = (
        _as_number(spend_limit.get("pooledLimit")) if isinstance(spend_limit, dict) else None
    )
    is_team = normalized_plan == "team" or (pooled_limit or 0) > 0

    if is_team and limit_cents and total_spend_cents is not None:
        _append_cursor_dollar_meter(
            meters, "total", "Total usage", total_spend_cents, limit_cents, cycle_end
        )
    elif total_percent is not None:
        _append_cursor_percent_meter(meters, "total", "Total usage", total_percent, cycle_end)
    elif limit_cents and total_spend_cents is not None:
        _append_cursor_dollar_meter(
            meters, "total", "Total usage", total_spend_cents, limit_cents, cycle_end
        )

    _append_cursor_percent_meter(
        meters, "auto", "Auto usage", plan_usage.get("autoPercentUsed"), cycle_end
    )
    _append_cursor_percent_meter(
        meters, "api", "API usage", plan_usage.get("apiPercentUsed"), cycle_end
    )

    if isinstance(spend_limit, dict):
        on_demand_limit = _as_number(spend_limit.get("individualLimit")) or _as_number(
            spend_limit.get("pooledLimit")
        )
        on_demand_remaining = _as_number(spend_limit.get("individualRemaining")) or _as_number(
            spend_limit.get("pooledRemaining")
        )
        if on_demand_limit and on_demand_limit > 0 and on_demand_remaining is not None:
            spent = max(0.0, on_demand_limit - on_demand_remaining)
            _append_cursor_dollar_meter(
                meters, "on_demand", "On-demand", spent, on_demand_limit, cycle_end
            )

    return ProviderUsage(
        provider="cursor",
        plan=plan_name,
        logged_in=True,
        meters=meters,
        breakdown=_scan_cursor_activity(),
    )


def _usage_cache_path() -> Path:
    return settings.repo_root / "data" / USAGE_CACHE_FILENAME


def _read_usage_cache() -> dict[str, dict[str, Any]]:
    path = _usage_cache_path()
    if not path.is_file():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.debug("could not read usage cache %s: %s", path, exc)
        return {}
    if not isinstance(raw, dict):
        return {}
    out: dict[str, dict[str, Any]] = {}
    for key, value in raw.items():
        if isinstance(key, str) and isinstance(value, dict):
            out[key] = value
    return out


def _write_usage_cache(cache: dict[str, dict[str, Any]]) -> None:
    path = _usage_cache_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(cache, indent=2) + "\n", encoding="utf-8")


def _meters_from_cache(rows: Any) -> list[UsageMeter]:
    if not isinstance(rows, list):
        return []
    meters: list[UsageMeter] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        meters.append(
            UsageMeter(
                key=str(row.get("key") or ""),
                label=str(row.get("label") or ""),
                used=float(row.get("used") or 0),
                limit=float(row["limit"]) if row.get("limit") is not None else None,
                unit=str(row.get("unit") or ""),
                percent_used=float(row["percent_used"])
                if row.get("percent_used") is not None
                else None,
                resets_at=row.get("resets_at"),
                status=str(row.get("status") or "ok"),
            )
        )
    return meters


def _breakdown_from_cache(rows: Any) -> list[UsageBreakdownItem]:
    if not isinstance(rows, list):
        return []
    items: list[UsageBreakdownItem] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        items.append(
            UsageBreakdownItem(
                name=str(row.get("name") or ""),
                amount=float(row.get("amount") or 0),
                unit=str(row.get("unit") or ""),
                share_percent=float(row.get("share_percent") or 0),
            )
        )
    return items


def _provider_from_cache_entry(data: dict[str, Any]) -> ProviderUsage:
    return ProviderUsage(
        provider=str(data.get("provider") or ""),
        plan=data.get("plan"),
        logged_in=bool(data.get("logged_in")),
        error=None,
        meters=_meters_from_cache(data.get("meters")),
        breakdown=_breakdown_from_cache(data.get("breakdown")),
        cached_at=data.get("cached_at"),
    )


def _resolve_provider_with_cache(
    provider: ProviderUsage,
    cache: dict[str, dict[str, Any]],
) -> tuple[ProviderUsage, dict[str, dict[str, Any]] | None]:
    if provider.error is None:
        cached_at = datetime.now(tz=timezone.utc).isoformat()
        return (
            ProviderUsage(
                provider=provider.provider,
                plan=provider.plan,
                logged_in=provider.logged_in,
                error=None,
                meters=provider.meters,
                breakdown=provider.breakdown,
                from_cache=False,
                cached_at=cached_at,
            ),
            {**provider.as_dict(), "cached_at": cached_at},
        )

    if not provider.logged_in:
        return provider, None

    cached_entry = cache.get(provider.provider)
    if not cached_entry:
        return provider, None

    cached_provider = _provider_from_cache_entry(cached_entry)
    if not cached_provider.meters:
        return provider, None

    return (
        ProviderUsage(
            provider=provider.provider,
            plan=provider.plan or cached_provider.plan,
            logged_in=provider.logged_in,
            error=provider.error,
            meters=cached_provider.meters,
            breakdown=provider.breakdown or cached_provider.breakdown,
            from_cache=True,
            cached_at=cached_provider.cached_at,
        ),
        None,
    )


def _merge_cache_entry(
    provider: ProviderUsage,
    cache_entry: dict[str, dict[str, Any]] | None,
) -> dict[str, Any]:
    merged: dict[str, Any] = dict(cache_entry or {})
    merged["provider"] = provider.provider
    if provider.rate_limited_until:
        merged["rate_limited_until"] = provider.rate_limited_until
    elif provider.error is None:
        merged.pop("rate_limited_until", None)
    return merged


def get_usage_snapshot() -> dict[str, Any]:
    cache = _read_usage_cache()
    with httpx.Client() as client:
        providers = [
            _fetch_claude_usage(client, cache.get("claude")),
            _fetch_cursor_usage(client, cache.get("cursor")),
        ]

    updated_cache = dict(cache)
    resolved_providers: list[ProviderUsage] = []
    for provider in providers:
        resolved, cache_entry = _resolve_provider_with_cache(provider, cache)
        resolved_providers.append(resolved)
        if cache_entry is not None:
            updated_cache[provider.provider] = _merge_cache_entry(provider, cache_entry)
        elif provider.rate_limited_until:
            updated_cache[provider.provider] = _merge_cache_entry(
                provider, updated_cache.get(provider.provider)
            )

    if updated_cache != cache:
        _write_usage_cache(updated_cache)

    warnings: list[str] = []
    near_limit = False
    for provider in resolved_providers:
        for meter in provider.meters:
            if meter.status == "warning":
                near_limit = True
                warnings.append(
                    f"{provider.provider.title()} {meter.label} is above {WARNING_PERCENT:.0f}%"
                )
            elif meter.status == "critical":
                near_limit = True
                warnings.append(
                    f"{provider.provider.title()} {meter.label} is above {CRITICAL_PERCENT:.0f}%"
                )
        if provider.from_cache and provider.cached_at:
            cached_time = datetime.fromisoformat(
                provider.cached_at.replace("Z", "+00:00")
            ).strftime("%Y-%m-%d %H:%M UTC")
            warnings.append(
                f"{provider.provider.title()}: live usage unavailable — showing cached data from {cached_time}"
            )
        elif provider.error and provider.logged_in:
            warnings.append(f"{provider.provider.title()}: {provider.error}")

    return {
        "providers": [provider.as_dict() for provider in resolved_providers],
        "near_limit": near_limit,
        "warnings": warnings,
        "fetched_at": datetime.now(tz=timezone.utc).isoformat(),
    }
