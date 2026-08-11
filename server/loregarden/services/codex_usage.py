"""Codex usage: live ChatGPT meters plus local activity from rollout transcripts.

Meters (weekly / session windows) come from
``GET https://chatgpt.com/backend-api/codex/usage`` using the tokens already in
``$CODEX_HOME/auth.json``. Those values are what ChatGPT's own usage page
shows; rollout ``rate_limits`` blocks are only a fallback when the network
call fails.

Per-model activity still comes from local
``$CODEX_HOME/sessions/**/rollout-*.jsonl`` transcripts. Each turn's
``last_token_usage`` is counted once as uncached work
(``input - cached_input + output``). Using ``total_token_usage``'s cumulative
``total_tokens`` would re-count the growing context on every turn and inflate
the breakdown by an order of magnitude.

``auth.json`` is read only for the bearer + account id headers; values are
never logged.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
from loregarden.services.codex_discovery import codex_home

logger = logging.getLogger(__name__)

CODEX_USAGE_URL = "https://chatgpt.com/backend-api/codex/usage"

# How many of the newest transcripts to search for a rate-limit reading before
# giving up. The newest session normally has one; a handful covers the case
# where the last runs died before their first token_count.
RATE_LIMIT_SEARCH_FILES = 6


def _sessions_root() -> Path:
    return codex_home() / "sessions"


def is_signed_in() -> bool:
    """Whether Codex has a stored credential, without reading it."""
    auth = codex_home() / "auth.json"
    try:
        return auth.is_file() and auth.stat().st_size > 0
    except OSError:
        return False


def auth_tokens() -> tuple[str, str] | None:
    """Return ``(access_token, account_id)`` for the live usage API, or None.

    Contents are never logged — callers must treat the return value as secret.
    """
    path = codex_home() / "auth.json"
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.debug("could not read codex auth.json: %s", exc)
        return None
    if not isinstance(raw, dict):  # py-org: allow-isinstance — auth.json is foreign JSON
        return None
    tokens = raw.get("tokens")
    if not isinstance(tokens, dict):  # py-org: allow-isinstance — auth.json is foreign JSON
        return None
    access = str(tokens.get("access_token") or "").strip()
    account = str(tokens.get("account_id") or "").strip()
    if not access or not account:
        return None
    return access, account


def _as_number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _rollout_files(*, since: float | None = None) -> list[Path]:
    """Rollout transcripts, newest first, optionally limited to a time window."""
    root = _sessions_root()
    if not root.is_dir():
        return []
    found: list[tuple[float, Path]] = []
    for path in root.rglob("rollout-*.jsonl"):
        try:
            mtime = path.stat().st_mtime
        except OSError:
            continue
        # A transcript last written before the window can only hold rows outside
        # it, so skip the read rather than parsing the whole history each poll.
        if since is not None and mtime < since:
            continue
        found.append((mtime, path))
    found.sort(key=lambda item: item[0], reverse=True)
    return [path for _, path in found]


def _read_lines(path: Path) -> list[str]:
    try:
        return path.read_text(encoding="utf-8", errors="ignore").splitlines()
    except OSError as exc:
        logger.debug("could not read codex rollout %s: %s", path, exc)
        return []


def _event_payload(line: str, event_type: str) -> dict[str, Any] | None:
    try:
        row = json.loads(line)
    except json.JSONDecodeError:
        return None
    if not isinstance(row, dict) or row.get("type") != "event_msg":
        return None
    payload = row.get("payload")
    if not isinstance(payload, dict) or payload.get("type") != event_type:
        return None
    return payload


def latest_rate_limits() -> tuple[dict[str, Any] | None, str | None]:
    """Newest ``rate_limits`` block Codex recorded, with its ISO timestamp."""
    for path in _rollout_files()[:RATE_LIMIT_SEARCH_FILES]:
        for line in reversed(_read_lines(path)):
            if '"rate_limits"' not in line:
                continue
            payload = _event_payload(line, "token_count")
            if payload is None:
                continue
            limits = payload.get("rate_limits")
            if isinstance(limits, dict):
                try:
                    observed = json.loads(line).get("timestamp")
                except json.JSONDecodeError:
                    observed = None
                return limits, observed if isinstance(observed, str) else None
    return None, None


def _window_from_api(window: Any) -> dict[str, Any] | None:
    """Normalize a live ``primary_window`` / ``secondary_window`` into local shape."""
    if not isinstance(window, dict):  # py-org: allow-isinstance — /codex/usage JSON
        return None
    used = _as_number(
        window.get("used_percent") if "used_percent" in window else window.get("usedPercent")
    )
    if used is None:
        return None
    seconds = _as_number(
        window.get("limit_window_seconds")
        if "limit_window_seconds" in window
        else window.get("limitWindowSeconds")
    )
    resets = window.get("reset_at") if "reset_at" in window else window.get("resetAt")
    if resets is None:
        resets = window.get("resets_at") if "resets_at" in window else window.get("resetsAt")
    minutes = None
    if seconds is not None and seconds > 0:
        minutes = int((seconds + 59) // 60)
    out: dict[str, Any] = {"used_percent": used}
    if minutes is not None:
        out["window_minutes"] = minutes
    if isinstance(resets, (int, float)):  # py-org: allow-isinstance — /codex/usage JSON
        out["resets_at"] = int(resets)
    return out


def limits_from_usage_body(body: Any) -> dict[str, Any] | None:
    """Map ``/codex/usage`` JSON onto the local ``rate_limits`` snapshot shape."""
    if not isinstance(body, dict):  # py-org: allow-isinstance — /codex/usage JSON
        return None
    rate_raw = body.get("rate_limit")
    rate = rate_raw if isinstance(rate_raw, dict) else body  # py-org: allow-isinstance
    if not isinstance(rate, dict):  # py-org: allow-isinstance — /codex/usage JSON
        return None
    primary = _window_from_api(
        rate.get("primary_window") or rate.get("primaryWindow") or rate.get("primary")
    )
    secondary = _window_from_api(
        rate.get("secondary_window") or rate.get("secondaryWindow") or rate.get("secondary")
    )
    plan = (
        body.get("plan_type")
        or body.get("planType")
        or rate.get("plan_type")
        or rate.get("planType")
    )
    if primary is None and secondary is None and not plan:
        return None
    return {
        "primary": primary,
        "secondary": secondary,
        "plan_type": str(plan).strip() if plan else None,
    }


def fetch_live_rate_limits(
    client: httpx.Client,
) -> tuple[dict[str, Any] | None, httpx.Response | None]:
    """Hit ChatGPT's Codex usage endpoint. Returns ``(limits, response)``.

    ``limits`` is None when auth is missing or the body cannot be parsed.
    ``response`` is None only when there were no credentials to send.
    """
    creds = auth_tokens()
    if creds is None:
        return None, None
    access, account_id = creds
    response = client.get(
        CODEX_USAGE_URL,
        headers={
            "Authorization": f"Bearer {access}",
            "ChatGPT-Account-Id": account_id,
            "Accept": "application/json",
            "User-Agent": "loregarden/0.1",
        },
        timeout=10,
    )
    if response.status_code >= 400:
        return None, response
    try:
        body = response.json()
    except (json.JSONDecodeError, ValueError):
        return None, response
    return limits_from_usage_body(body), response


def _turn_context_model(line: str) -> str | None:
    try:
        row = json.loads(line)
    except json.JSONDecodeError:
        return None
    if not isinstance(row, dict) or row.get("type") != "turn_context":
        return None
    payload = row.get("payload")
    named = str((payload or {}).get("model") or "").strip()
    return named or None


def _uncached_turn_tokens(line: str) -> float | None:
    """Tokens of new work on one turn — not the cumulative session total.

    ``total_token_usage.total_tokens`` restates the sum of every prior turn's
    full prompt (context grows), so taking its max over the file multiplies
    the same tokens by the turn count. ``last_token_usage`` is per-turn; drop
    the cached prefix so long chats don't look like 100M+ tokens of spend.
    """
    payload = _event_payload(line, "token_count")
    if payload is None:
        return None
    info = payload.get("info")
    if not isinstance(info, dict):  # py-org: allow-isinstance — rollout token_count JSON
        return None
    usage = info.get("last_token_usage")
    if not isinstance(usage, dict):  # py-org: allow-isinstance — rollout token_count JSON
        return None
    input_tokens = _as_number(usage.get("input_tokens")) or 0.0
    cached = _as_number(usage.get("cached_input_tokens")) or 0.0
    output = _as_number(usage.get("output_tokens")) or 0.0
    return max(0.0, input_tokens - cached) + output


def _session_usage(path: Path) -> tuple[str, float]:
    """One session's model and uncached token total."""
    model = "unknown"
    total = 0.0
    for line in _read_lines(path):
        if '"turn_context"' in line:
            model = _turn_context_model(line) or model
            continue
        if '"last_token_usage"' not in line:
            continue
        tokens = _uncached_turn_tokens(line)
        if tokens is not None:
            total += tokens
    return model, total


def model_token_totals(days_back: int = 7) -> dict[str, float]:
    """Uncached tokens per model across recent sessions."""
    since = datetime.now(tz=timezone.utc).timestamp() - days_back * 86400
    totals: dict[str, float] = {}
    for path in _rollout_files(since=since):
        model, session_total = _session_usage(path)
        if session_total > 0:
            totals[model] = totals.get(model, 0.0) + session_total
    return totals


def recent_model() -> str | None:
    """Most recent model named in a local transcript, if any."""
    for path in _rollout_files()[:RATE_LIMIT_SEARCH_FILES]:
        model = "unknown"
        for line in _read_lines(path):
            if '"turn_context"' not in line:
                continue
            model = _turn_context_model(line) or model
        if model != "unknown":
            return model
    return None


def plan_label(limits: dict[str, Any] | None) -> str | None:
    plan = str((limits or {}).get("plan_type") or "").strip()
    return plan.replace("_", " ").title() if plan else None


def window_label(minutes: Any) -> str:
    """Human label for a rate-limit window length (Codex reports minutes)."""
    value = _as_number(minutes)
    if value is None or value <= 0:
        return "Usage limit"
    total = int(value)
    if total % 10080 == 0:
        weeks = total // 10080
        return "Weekly" if weeks == 1 else f"{weeks}-weekly"
    if total % 1440 == 0:
        days = total // 1440
        return "Daily" if days == 1 else f"{days}-day"
    if total % 60 == 0:
        return f"Session ({total // 60}h)"
    return f"{total} min"
