"""Local Codex CLI usage, read from the rollout transcripts it already writes.

Codex has no public usage endpoint, but it records everything its own
``/status`` shows into ``$CODEX_HOME/sessions/<y>/<m>/<d>/rollout-*.jsonl``:

- ``event_msg`` rows of type ``token_count`` carry a ``rate_limits`` block with
  ``primary``/``secondary`` windows (``used_percent``, ``window_minutes``,
  ``resets_at``) and the account's ``plan_type``.
- The same rows carry ``info.total_token_usage``, which is cumulative for the
  session, and ``turn_context`` rows name the model that spent it.

Reading those files keeps this off the network entirely and — unlike the Claude
and Cursor providers — never touches a credential. Sign-in is inferred from the
mere existence of ``auth.json``; its contents are deliberately never read.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from loregarden.services.codex_discovery import codex_home

logger = logging.getLogger(__name__)

# How many of the newest transcripts to search for a rate-limit reading before
# giving up. The newest session normally has one; a handful of covers the case
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


def _cumulative_tokens(line: str) -> float | None:
    payload = _event_payload(line, "token_count")
    if payload is None:
        return None
    info = payload.get("info")
    usage = info.get("total_token_usage") if isinstance(info, dict) else None
    if not isinstance(usage, dict):
        return None
    return _as_number(usage.get("total_tokens"))


def _session_usage(path: Path) -> tuple[str, float]:
    """One session's model and total tokens.

    ``total_token_usage`` is cumulative within a session, so the largest value
    in the file is the session total — summing every ``token_count`` event would
    multiply-count it. The session is attributed to the last model its
    ``turn_context`` rows named, which did most of the work when a model switch
    happened mid-session.
    """
    model = "unknown"
    total = 0.0
    for line in _read_lines(path):
        if '"turn_context"' in line:
            model = _turn_context_model(line) or model
            continue
        if '"total_token_usage"' not in line:
            continue
        tokens = _cumulative_tokens(line)
        if tokens is not None and tokens > total:
            total = tokens
    return model, total


def model_token_totals(days_back: int = 7) -> dict[str, float]:
    """Total tokens per model across recent sessions."""
    since = datetime.now(tz=timezone.utc).timestamp() - days_back * 86400
    totals: dict[str, float] = {}
    for path in _rollout_files(since=since):
        model, session_total = _session_usage(path)
        if session_total > 0:
            totals[model] = totals.get(model, 0.0) + session_total
    return totals


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
