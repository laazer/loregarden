import json
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from loregarden.services import codex_usage, usage_service


def _rollout(
    tmp_path,
    name: str,
    *,
    model: str,
    total_tokens: int,
    used_percent: float | None = 42.0,
    window_minutes: int = 10080,
    timestamp: str = "2026-08-08T12:33:25.191Z",
) -> None:
    """One transcript in the shape Codex actually writes."""
    session_dir = tmp_path / "sessions" / "2026" / "08" / "08"
    session_dir.mkdir(parents=True, exist_ok=True)
    rows = [
        {"type": "session_meta", "payload": {"session_id": name}},
        {"type": "turn_context", "payload": {"model": model, "effort": "medium"}},
    ]
    # Cumulative counts: only the last one is the session total.
    for step in (total_tokens // 2, total_tokens):
        rows.append(
            {
                "timestamp": timestamp,
                "type": "event_msg",
                "payload": {
                    "type": "token_count",
                    "info": {"total_token_usage": {"total_tokens": step}},
                    "rate_limits": None
                    if used_percent is None
                    else {
                        "primary": {
                            "used_percent": used_percent,
                            "window_minutes": window_minutes,
                            "resets_at": 1786543666,
                        },
                        "secondary": None,
                        "plan_type": "plus",
                    },
                },
            }
        )
    (session_dir / f"rollout-{name}.jsonl").write_text(
        "\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8"
    )


def _sign_in(tmp_path) -> None:
    (tmp_path / "auth.json").write_text('{"tokens": {}}', encoding="utf-8")


def test_signed_in_never_reads_the_credential(tmp_path):
    """Sign-in is inferred from the file's existence — the contents of auth.json
    hold live OpenAI tokens and must not be parsed into the usage payload."""
    _sign_in(tmp_path)

    with patch.object(codex_usage, "codex_home", return_value=tmp_path):
        assert codex_usage.is_signed_in() is True
        (tmp_path / "auth.json").write_text("", encoding="utf-8")
        assert codex_usage.is_signed_in() is False


def test_latest_rate_limits_reads_the_newest_reading(tmp_path):
    _rollout(tmp_path, "old", model="gpt-5.5", total_tokens=100, used_percent=10.0)
    _rollout(tmp_path, "new", model="gpt-5.5", total_tokens=200, used_percent=84.0)
    newest = tmp_path / "sessions/2026/08/08/rollout-new.jsonl"
    future = (datetime.now(tz=timezone.utc) + timedelta(minutes=5)).timestamp()
    import os

    os.utime(newest, (future, future))

    with patch.object(codex_usage, "codex_home", return_value=tmp_path):
        limits, observed = codex_usage.latest_rate_limits()

    assert limits["primary"]["used_percent"] == 84.0
    assert observed == "2026-08-08T12:33:25.191Z"


def test_model_totals_do_not_multiply_count_cumulative_events(tmp_path):
    """``total_token_usage`` restates the session total on every event; summing
    them would report several times the tokens actually spent."""
    _rollout(tmp_path, "a", model="gpt-5.5", total_tokens=1000)
    _rollout(tmp_path, "b", model="gpt-5.6-sol", total_tokens=500)

    with patch.object(codex_usage, "codex_home", return_value=tmp_path):
        totals = codex_usage.model_token_totals()

    assert totals == {"gpt-5.5": 1000.0, "gpt-5.6-sol": 500.0}


def test_model_totals_apply_the_days_back_window(tmp_path):
    import os

    _rollout(tmp_path, "fresh", model="gpt-5.5", total_tokens=100)
    _rollout(tmp_path, "stale", model="gpt-5.6-sol", total_tokens=900)
    stale = tmp_path / "sessions/2026/08/08/rollout-stale.jsonl"
    old = (datetime.now(tz=timezone.utc) - timedelta(days=30)).timestamp()
    os.utime(stale, (old, old))

    with patch.object(codex_usage, "codex_home", return_value=tmp_path):
        totals = codex_usage.model_token_totals(days_back=7)

    assert totals == {"gpt-5.5": 100.0}


def test_window_labels_match_the_windows_codex_reports():
    assert codex_usage.window_label(10080) == "Weekly"
    assert codex_usage.window_label(1440) == "Daily"
    assert codex_usage.window_label(300) == "Session (5h)"
    assert codex_usage.window_label(None) == "Usage limit"


def test_fetch_codex_usage_builds_meters_plan_and_breakdown(tmp_path):
    _sign_in(tmp_path)
    _rollout(tmp_path, "a", model="gpt-5.5", total_tokens=800, used_percent=84.0)
    _rollout(tmp_path, "b", model="gpt-5.6-sol", total_tokens=200, used_percent=84.0)

    with patch.object(codex_usage, "codex_home", return_value=tmp_path):
        provider = usage_service._fetch_codex_usage()

    assert provider.logged_in is True
    assert provider.plan == "Plus"
    assert [(m.key, m.label, m.used, m.status) for m in provider.meters] == [
        ("primary", "Weekly", 84.0, "warning")
    ]
    assert [(b.name, b.amount) for b in provider.breakdown] == [
        ("gpt-5.5", 800.0),
        ("gpt-5.6-sol", 200.0),
    ]
    assert round(sum(b.share_percent for b in provider.breakdown), 1) == 100.0
    # The reading's own age, not the poll time — Codex only refreshes it on a run.
    assert provider.observed_at == "2026-08-08T12:33:25.191000+00:00"


def test_fetch_codex_usage_reports_not_logged_in_without_auth_file(tmp_path):
    with patch.object(codex_usage, "codex_home", return_value=tmp_path):
        provider = usage_service._fetch_codex_usage()

    assert provider.logged_in is False
    assert "codex login" in provider.error


def test_fetch_codex_usage_distinguishes_signed_in_but_never_run(tmp_path):
    _sign_in(tmp_path)

    with patch.object(codex_usage, "codex_home", return_value=tmp_path):
        provider = usage_service._fetch_codex_usage()

    assert provider.logged_in is True
    assert provider.meters == []
    assert "No usage recorded yet" in provider.error


def test_snapshot_includes_codex_and_the_configured_model(tmp_path, monkeypatch):
    cache_path = tmp_path / "data" / usage_service.USAGE_CACHE_FILENAME
    monkeypatch.setattr(usage_service, "_usage_cache_path", lambda: cache_path)
    monkeypatch.setattr(
        usage_service,
        "_fetch_claude_usage",
        lambda client, cache_entry=None: usage_service.ProviderUsage(
            provider="claude", logged_in=False
        ),
    )
    monkeypatch.setattr(
        usage_service,
        "_fetch_cursor_usage",
        lambda client, cache_entry=None: usage_service.ProviderUsage(
            provider="cursor", logged_in=False
        ),
    )
    monkeypatch.setattr(
        usage_service,
        "_fetch_codex_usage",
        lambda: usage_service.ProviderUsage(provider="codex", plan="Plus", logged_in=True),
    )
    monkeypatch.setenv("LOREGARDEN_CODEX_MODEL", "gpt-5.6-sol")
    monkeypatch.setenv("LOREGARDEN_CLI_ADAPTER", "codex")

    snapshot = usage_service.get_usage_snapshot()

    codex = next(p for p in snapshot["providers"] if p["provider"] == "codex")
    assert codex["plan"] == "Plus"
    assert codex["configured_model"] == "gpt-5.6-sol"
    assert codex["active_adapter"] is True
    claude = next(p for p in snapshot["providers"] if p["provider"] == "claude")
    assert claude["active_adapter"] is False


def test_stale_codex_reading_is_called_out(tmp_path, monkeypatch):
    """The numbers only move when Codex runs, so a week-old reading shown without
    comment reads as current usage."""
    cache_path = tmp_path / "data" / usage_service.USAGE_CACHE_FILENAME
    monkeypatch.setattr(usage_service, "_usage_cache_path", lambda: cache_path)
    stale = (datetime.now(tz=timezone.utc) - timedelta(days=3)).isoformat()
    for name in ("_fetch_claude_usage", "_fetch_cursor_usage"):
        monkeypatch.setattr(
            usage_service,
            name,
            lambda client, cache_entry=None, provider=name: usage_service.ProviderUsage(
                provider="claude" if "claude" in provider else "cursor", logged_in=False
            ),
        )
    monkeypatch.setattr(
        usage_service,
        "_fetch_codex_usage",
        lambda: usage_service.ProviderUsage(
            provider="codex",
            logged_in=True,
            observed_at=stale,
            meters=[
                usage_service.UsageMeter(
                    key="primary", label="Weekly", used=10.0, limit=100.0, unit="percent"
                )
            ],
        ),
    )

    snapshot = usage_service.get_usage_snapshot()

    assert any("last recorded" in warning for warning in snapshot["warnings"])
