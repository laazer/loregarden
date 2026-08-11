import json
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import httpx
from loregarden.services import codex_usage, usage_service


def _rollout(
    tmp_path,
    name: str,
    *,
    model: str,
    turn_tokens: list[tuple[int, int, int]],
    used_percent: float | None = 42.0,
    window_minutes: int = 10080,
    timestamp: str = "2026-08-08T12:33:25.191Z",
) -> None:
    """One transcript in the shape Codex actually writes.

    ``turn_tokens`` is a list of ``(input, cached_input, output)`` per turn.
    ``total_token_usage`` is written cumulatively (the old trap); the reader
    must ignore it and sum uncached ``last_token_usage`` instead.
    """
    session_dir = tmp_path / "sessions" / "2026" / "08" / "08"
    session_dir.mkdir(parents=True, exist_ok=True)
    rows: list[dict] = [
        {"type": "session_meta", "payload": {"session_id": name}},
        {"type": "turn_context", "payload": {"model": model, "effort": "medium"}},
    ]
    cumulative = 0
    for input_tokens, cached, output in turn_tokens:
        cumulative += input_tokens + output
        rows.append(
            {
                "timestamp": timestamp,
                "type": "event_msg",
                "payload": {
                    "type": "token_count",
                    "info": {
                        "total_token_usage": {"total_tokens": cumulative},
                        "last_token_usage": {
                            "input_tokens": input_tokens,
                            "cached_input_tokens": cached,
                            "output_tokens": output,
                            "total_tokens": input_tokens + output,
                        },
                    },
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


def _sign_in(tmp_path, *, with_tokens: bool = True) -> None:
    if with_tokens:
        payload = {
            "tokens": {
                "access_token": "test-access",
                "account_id": "test-account",
                "refresh_token": "test-refresh",
            }
        }
    else:
        payload = {"tokens": {}}
    (tmp_path / "auth.json").write_text(json.dumps(payload), encoding="utf-8")


def _mock_response(status: int, body: dict | None = None) -> MagicMock:
    response = MagicMock()
    response.status_code = status
    response.json.return_value = body or {}
    response.headers = {}
    response.text = json.dumps(body or {})
    return response


def test_signed_in_never_reads_the_credential_for_presence(tmp_path):
    """Sign-in presence is inferred from the file — live fetch is the only
    path that parses tokens, and those values must not reach logs or payloads."""
    _sign_in(tmp_path)

    with patch.object(codex_usage, "codex_home", return_value=tmp_path):
        assert codex_usage.is_signed_in() is True
        (tmp_path / "auth.json").write_text("", encoding="utf-8")
        assert codex_usage.is_signed_in() is False


def test_latest_rate_limits_reads_the_newest_reading(tmp_path):
    _rollout(tmp_path, "old", model="gpt-5.5", turn_tokens=[(100, 0, 0)], used_percent=10.0)
    _rollout(tmp_path, "new", model="gpt-5.5", turn_tokens=[(200, 0, 0)], used_percent=84.0)
    newest = tmp_path / "sessions/2026/08/08/rollout-new.jsonl"
    future = (datetime.now(tz=timezone.utc) + timedelta(minutes=5)).timestamp()
    import os

    os.utime(newest, (future, future))

    with patch.object(codex_usage, "codex_home", return_value=tmp_path):
        limits, observed = codex_usage.latest_rate_limits()

    assert limits["primary"]["used_percent"] == 84.0
    assert observed == "2026-08-08T12:33:25.191Z"


def test_model_totals_count_uncached_turn_tokens_not_cumulative_context(tmp_path):
    """``total_token_usage`` restates the growing context on every turn.
    Counting its max (or summing it) reports many times the tokens spent."""
    # Two turns: first 100 in / 10 out; second 1000 in (900 cached) / 20 out.
    # Uncached = (100-0+10) + (1000-900+20) = 230. Cumulative max would be 1130.
    _rollout(
        tmp_path,
        "a",
        model="gpt-5.5",
        turn_tokens=[(100, 0, 10), (1000, 900, 20)],
    )
    _rollout(
        tmp_path,
        "b",
        model="gpt-5.6-sol",
        turn_tokens=[(500, 100, 50)],
    )

    with patch.object(codex_usage, "codex_home", return_value=tmp_path):
        totals = codex_usage.model_token_totals()

    assert totals == {"gpt-5.5": 230.0, "gpt-5.6-sol": 450.0}


def test_model_totals_apply_the_days_back_window(tmp_path):
    import os

    _rollout(tmp_path, "fresh", model="gpt-5.5", turn_tokens=[(100, 0, 0)])
    _rollout(tmp_path, "stale", model="gpt-5.6-sol", turn_tokens=[(900, 0, 0)])
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


def test_limits_from_usage_body_maps_api_windows():
    limits = codex_usage.limits_from_usage_body(
        {
            "plan_type": "plus",
            "rate_limit": {
                "primary_window": {
                    "used_percent": 42,
                    "limit_window_seconds": 604800,
                    "reset_at": 1786910549,
                },
                "secondary_window": {
                    "used_percent": 10,
                    "limit_window_seconds": 18000,
                    "reset_at": 1786400000,
                },
            },
        }
    )
    assert limits["plan_type"] == "plus"
    assert limits["primary"] == {
        "used_percent": 42.0,
        "window_minutes": 10080,
        "resets_at": 1786910549,
    }
    assert limits["secondary"]["window_minutes"] == 300
    assert limits["secondary"]["used_percent"] == 10.0


def test_fetch_codex_usage_prefers_live_api_meters(tmp_path):
    _sign_in(tmp_path)
    _rollout(
        tmp_path,
        "a",
        model="gpt-5.5",
        turn_tokens=[(800, 100, 50)],
        used_percent=99.0,  # stale local reading — must not win over the API
    )
    live = _mock_response(
        200,
        {
            "plan_type": "plus",
            "rate_limit": {
                "primary_window": {
                    "used_percent": 12,
                    "limit_window_seconds": 604800,
                    "reset_at": 1786910549,
                },
                "secondary_window": None,
            },
        },
    )
    client = MagicMock()
    client.get.return_value = live

    with patch.object(codex_usage, "codex_home", return_value=tmp_path):
        provider = usage_service._fetch_codex_usage(client)

    assert provider.logged_in is True
    assert provider.plan == "Plus"
    assert provider.error is None
    assert [(m.key, m.label, m.used, m.status) for m in provider.meters] == [
        ("primary", "Weekly", 12.0, "ok")
    ]
    # Uncached: (800-100+50) = 750
    assert [(b.name, b.amount) for b in provider.breakdown] == [("gpt-5.5", 750.0)]
    client.get.assert_called_once()
    assert client.get.call_args.args[0] == codex_usage.CODEX_USAGE_URL


def test_fetch_codex_usage_falls_back_to_local_on_http_error(tmp_path):
    _sign_in(tmp_path)
    _rollout(tmp_path, "a", model="gpt-5.5", turn_tokens=[(100, 0, 0)], used_percent=84.0)
    client = MagicMock()
    client.get.return_value = _mock_response(503, {"detail": "down"})

    with patch.object(codex_usage, "codex_home", return_value=tmp_path):
        provider = usage_service._fetch_codex_usage(client)

    assert provider.logged_in is True
    assert provider.meters[0].used == 84.0
    assert provider.error is not None
    assert "503" in provider.error


def test_fetch_codex_usage_falls_back_to_local_on_network_error(tmp_path):
    _sign_in(tmp_path)
    _rollout(tmp_path, "a", model="gpt-5.5", turn_tokens=[(100, 0, 0)], used_percent=84.0)
    client = MagicMock()
    client.get.side_effect = httpx.ConnectError("boom")

    with patch.object(codex_usage, "codex_home", return_value=tmp_path):
        provider = usage_service._fetch_codex_usage(client)

    assert provider.logged_in is True
    assert provider.meters[0].used == 84.0
    assert provider.error is not None
    assert "unreachable" in provider.error


def test_fetch_codex_usage_reports_not_logged_in_without_auth_file(tmp_path):
    client = MagicMock()
    with patch.object(codex_usage, "codex_home", return_value=tmp_path):
        provider = usage_service._fetch_codex_usage(client)

    assert provider.logged_in is False
    assert "codex login" in provider.error
    client.get.assert_not_called()


def test_fetch_codex_usage_distinguishes_signed_in_but_never_run(tmp_path):
    _sign_in(tmp_path, with_tokens=False)
    client = MagicMock()

    with patch.object(codex_usage, "codex_home", return_value=tmp_path):
        provider = usage_service._fetch_codex_usage(client)

    assert provider.logged_in is True
    assert provider.meters == []
    assert "No usage recorded yet" in provider.error
    client.get.assert_not_called()


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
        lambda client, cache_entry=None: usage_service.ProviderUsage(
            provider="codex", plan="Plus", logged_in=True
        ),
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
    """A week-old local fallback shown without comment reads as current usage."""
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
        lambda client, cache_entry=None: usage_service.ProviderUsage(
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
