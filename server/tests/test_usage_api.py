import json
import os
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import httpx
from fastapi.testclient import TestClient
from loregarden.services import usage_service


class _FakeResponse:
    status_code = 500

    def json(self) -> dict:
        return {}


class _FakeHttpClient:
    """Stand-in for httpx.Client that never hits the network."""

    def __init__(self, *args, **kwargs) -> None:
        pass

    def __enter__(self) -> "_FakeHttpClient":
        return self

    def __exit__(self, *args) -> bool:
        return False

    def get(self, *args, **kwargs) -> _FakeResponse:
        return _FakeResponse()

    def post(self, *args, **kwargs) -> _FakeResponse:
        return _FakeResponse()


def test_usage_snapshot_never_leaks_access_token(monkeypatch):
    """A live OAuth token must never surface in the (unauthenticated) usage payload."""
    sentinel = "sk-ant-oauth-SENTINEL-TOKEN-abcdef0123456789"
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", sentinel)
    # Force the token to load from env and keep the fetch off the network.
    monkeypatch.setattr(usage_service, "_read_claude_credentials_file", lambda: None)
    monkeypatch.setattr(usage_service, "_read_claude_keychain_credentials", lambda: None)
    monkeypatch.setattr(usage_service.httpx, "Client", _FakeHttpClient)

    snapshot = usage_service.get_usage_snapshot()

    # The claude provider is exercised with a real token loaded; it must not appear.
    serialized = json.dumps(snapshot)
    assert sentinel not in serialized
    claude = next(p for p in snapshot["providers"] if p["provider"] == "claude")
    assert claude["logged_in"] is True


def test_claude_oauth_reads_cached_token_file_when_no_env_var(monkeypatch):
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
    monkeypatch.setattr(usage_service, "_read_claude_credentials_file", lambda: None)
    monkeypatch.setattr(usage_service, "_read_claude_keychain_credentials", lambda: None)
    monkeypatch.setattr(usage_service, "_read_claude_oauth_token_file", lambda: "cached-token")

    oauth = usage_service._claude_oauth()

    assert oauth == {"accessToken": "cached-token"}


def test_claude_oauth_token_file_persists_across_restarts(tmp_path, monkeypatch):
    """The whole point of the cache file is that it survives without re-exporting env vars."""
    token_path = tmp_path / "data" / ".claude-oauth-token"
    token_path.parent.mkdir(parents=True)
    token_path.write_text("  saved-token  \n", encoding="utf-8")
    monkeypatch.setattr(usage_service, "claude_oauth_token_file_path", lambda: token_path)
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
    monkeypatch.setattr(usage_service, "_read_claude_credentials_file", lambda: None)
    monkeypatch.setattr(usage_service, "_read_claude_keychain_credentials", lambda: None)

    oauth = usage_service._claude_oauth()

    assert oauth == {"accessToken": "saved-token"}


def test_claude_oauth_ignores_cached_file_holding_captured_terminal_output(tmp_path, monkeypatch):
    """`claude setup-token`'s interactive UI (spinners, prompts) can end up in the
    file if piped in naively — that must be treated as absent, not fed to httpx as
    a bearer token (which previously crashed with UnicodeEncodeError on the
    non-ASCII spinner glyphs)."""
    token_path = tmp_path / "data" / ".claude-oauth-token"
    token_path.parent.mkdir(parents=True)
    token_path.write_text("Setting up token\n✳ working …\nsome-token-fragment\n", encoding="utf-8")
    monkeypatch.setattr(usage_service, "claude_oauth_token_file_path", lambda: token_path)
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
    monkeypatch.setattr(usage_service, "_read_claude_credentials_file", lambda: None)
    monkeypatch.setattr(usage_service, "_read_claude_keychain_credentials", lambda: None)

    oauth = usage_service._claude_oauth()

    assert oauth is None


def test_claude_login_diagnosis_distinguishes_logged_out_from_unreadable():
    """A readable Keychain item with empty token fields is a logged-out session,
    not a Keychain ACL denial — it was previously reported as the latter, which
    sent users at a workaround that could not fix it."""
    empty_tokens = {"claudeAiOauth": {"accessToken": "", "refreshToken": "", "scopes": []}}

    with patch.object(
        usage_service, "_read_claude_keychain_credentials", return_value=empty_tokens
    ):
        message = usage_service._claude_login_diagnosis()

    assert "logged out" in message.lower()
    assert "claude /login" in message


def test_claude_login_diagnosis_reports_unreadable_keychain():
    with (
        patch.object(usage_service, "_read_claude_keychain_credentials", return_value=None),
        patch.object(usage_service, "_claude_keychain_item_exists", return_value=True),
    ):
        message = usage_service._claude_login_diagnosis()

    assert "unreadable" in message.lower()


def test_claude_login_diagnosis_never_suggests_setup_token():
    """`claude setup-token` mints an inference-scoped token; the usage endpoint
    answers 403 `does not meet scope requirement user:profile`, so pointing
    users there is a dead end no matter which state they're in."""
    states = [
        ({"claudeAiOauth": {"accessToken": ""}}, True),
        (None, True),
        (None, False),
    ]
    for keychain, item_exists in states:
        with (
            patch.object(usage_service, "_read_claude_keychain_credentials", return_value=keychain),
            patch.object(usage_service, "_claude_keychain_item_exists", return_value=item_exists),
        ):
            message = usage_service._claude_login_diagnosis()

        assert "setup-token" not in message
        # Renders in a 240px-wide UI slot next to the provider title
        # (.usage-provider-error in client/src/index.css), not a details panel.
        assert len(message) < 100


def test_usage_endpoint_returns_snapshot(client: TestClient):
    snapshot = {
        "providers": [
            {
                "provider": "claude",
                "plan": "Max 20x",
                "logged_in": True,
                "error": None,
                "meters": [
                    {
                        "key": "five_hour",
                        "label": "Session (5h)",
                        "used": 42.0,
                        "limit": 100.0,
                        "unit": "percent",
                        "percent_used": 42.0,
                        "resets_at": None,
                        "status": "ok",
                    }
                ],
                "breakdown": [
                    {
                        "name": "claude-sonnet-4-6",
                        "amount": 1200,
                        "unit": "tokens",
                        "share_percent": 100,
                    }
                ],
            },
            {
                "provider": "cursor",
                "plan": "Ultra",
                "logged_in": True,
                "error": None,
                "meters": [],
                "breakdown": [],
            },
        ],
        "near_limit": False,
        "warnings": [],
        "fetched_at": "2026-07-05T20:00:00+00:00",
    }
    with patch("loregarden.api.usage.get_usage_snapshot", return_value=snapshot):
        res = client.get("/api/usage")
    assert res.status_code == 200
    assert res.json() == snapshot


def test_meter_status_thresholds():
    assert usage_service._meter_status(79.9) == "ok"
    assert usage_service._meter_status(85.0) == "warning"
    assert usage_service._meter_status(95.0) == "critical"


def test_usage_snapshot_flags_near_limit():
    providers = [
        usage_service.ProviderUsage(
            provider="claude",
            logged_in=True,
            meters=[
                usage_service.UsageMeter(
                    key="seven_day",
                    label="Weekly",
                    used=92,
                    limit=100,
                    unit="percent",
                    percent_used=92,
                    status="critical",
                )
            ],
        )
    ]
    warnings: list[str] = []
    near_limit = False
    for provider in providers:
        for meter in provider.meters:
            if meter.status in {"warning", "critical"}:
                near_limit = True
                warnings.append(f"{provider.provider.title()} {meter.label} is high")
    assert near_limit is True
    assert warnings


def test_usage_cache_stores_successful_provider(tmp_path, monkeypatch):
    cache_path = tmp_path / "data" / usage_service.USAGE_CACHE_FILENAME
    monkeypatch.setattr(usage_service, "_usage_cache_path", lambda: cache_path)

    success = usage_service.ProviderUsage(
        provider="claude",
        plan="Max 20x",
        logged_in=True,
        meters=[
            usage_service.UsageMeter(
                key="five_hour",
                label="Session (5h)",
                used=42.0,
                limit=100.0,
                unit="percent",
                percent_used=42.0,
                status="ok",
            )
        ],
    )
    failure = usage_service.ProviderUsage(
        provider="cursor",
        logged_in=True,
        error="Usage request failed (HTTP 500).",
    )

    monkeypatch.setattr(
        usage_service,
        "_fetch_claude_usage",
        lambda client, cache_entry=None: success,
    )
    monkeypatch.setattr(
        usage_service,
        "_fetch_cursor_usage",
        lambda client, cache_entry=None: failure,
    )

    snapshot = usage_service.get_usage_snapshot()

    assert cache_path.is_file()
    cached = json.loads(cache_path.read_text(encoding="utf-8"))
    assert cached["claude"]["plan"] == "Max 20x"
    assert cached["claude"]["meters"][0]["key"] == "five_hour"
    assert "cursor" not in cached

    claude = next(p for p in snapshot["providers"] if p["provider"] == "claude")
    assert claude["from_cache"] is False
    assert claude["cached_at"] is not None
    assert claude["meters"][0]["used"] == 42.0


def test_usage_cache_fallback_on_api_error(tmp_path, monkeypatch):
    cache_path = tmp_path / "data" / usage_service.USAGE_CACHE_FILENAME
    monkeypatch.setattr(usage_service, "_usage_cache_path", lambda: cache_path)
    cache_path.parent.mkdir(parents=True)
    cache_path.write_text(
        json.dumps(
            {
                "claude": {
                    "provider": "claude",
                    "plan": "Max 20x",
                    "logged_in": True,
                    "error": None,
                    "meters": [
                        {
                            "key": "five_hour",
                            "label": "Session (5h)",
                            "used": 55.0,
                            "limit": 100.0,
                            "unit": "percent",
                            "percent_used": 55.0,
                            "resets_at": None,
                            "status": "ok",
                        }
                    ],
                    "breakdown": [],
                    "from_cache": False,
                    "cached_at": "2026-07-05T20:00:00+00:00",
                }
            }
        ),
        encoding="utf-8",
    )

    failure = usage_service.ProviderUsage(
        provider="claude",
        plan="Max 20x",
        logged_in=True,
        error="Usage request failed (HTTP 500).",
    )
    cursor = usage_service.ProviderUsage(
        provider="cursor",
        logged_in=False,
        error="Not logged in to Cursor.",
    )

    monkeypatch.setattr(
        usage_service, "_fetch_claude_usage", lambda client, cache_entry=None: failure
    )
    monkeypatch.setattr(
        usage_service, "_fetch_cursor_usage", lambda client, cache_entry=None: cursor
    )

    snapshot = usage_service.get_usage_snapshot()
    claude = next(p for p in snapshot["providers"] if p["provider"] == "claude")

    assert claude["from_cache"] is True
    assert claude["cached_at"] == "2026-07-05T20:00:00+00:00"
    assert claude["error"] == "Usage request failed (HTTP 500)."
    assert claude["meters"][0]["used"] == 55.0
    assert any("cached data" in warning.lower() for warning in snapshot["warnings"])


def test_format_usage_http_error_for_claude_rate_limit():
    response = httpx.Response(
        429,
        headers={"retry-after": "188"},
        json={
            "error": {
                "type": "rate_limit_error",
                "message": "Rate limited. Please try again later.",
            }
        },
        request=httpx.Request("GET", usage_service.CLAUDE_USAGE_URL),
    )
    message = usage_service._format_usage_http_error("claude", response)
    assert "Claude usage API rate limited" in message
    assert "Rate limited" in message
    assert "3 min" in message


def test_format_usage_http_error_for_claude_unauthorized():
    response = httpx.Response(
        401,
        request=httpx.Request("GET", usage_service.CLAUDE_USAGE_URL),
    )
    message = usage_service._format_usage_http_error("claude", response)
    assert message == "Claude session expired — run `claude` to re-authenticate."


def test_format_usage_http_error_for_claude_unauthorized_without_refresh_token():
    """A bare cached/env token can't be silently refreshed, so re-running `claude`
    (which only touches the interactive keychain session) wouldn't fix it — the
    message must point at regenerating the cached token instead."""
    response = httpx.Response(
        401,
        request=httpx.Request("GET", usage_service.CLAUDE_USAGE_URL),
    )
    message = usage_service._format_usage_http_error("claude", response, has_refresh_token=False)
    assert "claude:setup-token" in message
    assert "run `claude` to re-authenticate" not in message
    assert len(message) < 100


def test_format_usage_http_error_for_claude_forbidden_without_refresh_token_explains_scope():
    """A `claude setup-token` credential is scoped to inference only — a 403 here
    is an inherent scope limitation, not something regenerating the token fixes,
    so the message must not suggest that."""
    response = httpx.Response(
        403,
        request=httpx.Request("GET", usage_service.CLAUDE_USAGE_URL),
    )
    message = usage_service._format_usage_http_error("claude", response, has_refresh_token=False)
    assert "scoped to inference only" in message
    assert "regenerate" not in message.lower()
    assert len(message) < 100


def test_format_usage_http_error_for_claude_forbidden_with_refresh_token_is_generic():
    """A full interactive-login credential getting a 403 is not the known scope
    limitation, so it should fall back to the generic HTTP-status message."""
    response = httpx.Response(
        403,
        request=httpx.Request("GET", usage_service.CLAUDE_USAGE_URL),
    )
    message = usage_service._format_usage_http_error("claude", response, has_refresh_token=True)
    assert message == "Claude usage request failed (HTTP 403)."


def test_fetch_claude_usage_points_at_cached_token_regen_on_401_without_refresh(monkeypatch):
    monkeypatch.setattr(usage_service, "_claude_oauth", lambda: {"accessToken": "cached-token"})
    monkeypatch.setattr(
        usage_service,
        "_claude_usage_request",
        lambda client, access_token: httpx.Response(
            401, request=httpx.Request("GET", usage_service.CLAUDE_USAGE_URL)
        ),
    )
    monkeypatch.setattr(usage_service, "_scan_claude_logs", lambda: [])

    with httpx.Client() as client:
        result = usage_service._fetch_claude_usage(client)

    assert result.logged_in is True
    assert "claude:setup-token" in result.error
    assert "run `claude` to re-authenticate" not in result.error


def test_fetch_claude_usage_retries_after_unauthorized(monkeypatch):
    oauth = {
        "accessToken": "stale-token",
        "refreshToken": "refresh-token",
        "subscriptionType": "max",
    }
    calls = {"usage": 0, "refresh": 0}

    def fake_refresh(current_oauth, client):
        calls["refresh"] += 1
        return {**current_oauth, "accessToken": "fresh-token"}

    def fake_usage_request(client, access_token):
        calls["usage"] += 1
        if calls["usage"] == 1:
            assert access_token == "stale-token"
            return httpx.Response(
                401,
                request=httpx.Request("GET", usage_service.CLAUDE_USAGE_URL),
            )
        assert access_token == "fresh-token"
        return httpx.Response(
            200,
            json={"five_hour": {"utilization": 12.5}},
            request=httpx.Request("GET", usage_service.CLAUDE_USAGE_URL),
        )

    monkeypatch.setattr(usage_service, "_claude_oauth", lambda: oauth)
    monkeypatch.setattr(usage_service, "_refresh_claude_token", fake_refresh)
    monkeypatch.setattr(usage_service, "_claude_usage_request", fake_usage_request)
    monkeypatch.setattr(usage_service, "_scan_claude_logs", lambda: [])

    with httpx.Client() as client:
        result = usage_service._fetch_claude_usage(client)

    assert calls["refresh"] == 1
    assert calls["usage"] == 2
    assert result.error is None
    assert result.logged_in is True
    assert result.meters


def test_fetch_claude_usage_reports_plain_not_logged_in_when_keychain_item_absent(monkeypatch):
    monkeypatch.setattr(usage_service, "_claude_oauth", lambda: None)
    monkeypatch.setattr(usage_service, "_read_claude_keychain_credentials", lambda: None)
    monkeypatch.setattr(usage_service, "_claude_keychain_item_exists", lambda: False)
    monkeypatch.setattr(usage_service, "_scan_claude_logs", lambda *a, **k: [])
    monkeypatch.setattr(usage_service.claude_session_usage, "fetch_usage_body", lambda client: None)

    result = usage_service._fetch_claude_usage(_FakeHttpClient())

    assert result.logged_in is False
    assert result.error == "Not logged in. Run `claude` to authenticate."


def test_fetch_claude_usage_reports_keychain_access_issue_when_item_unreadable(monkeypatch):
    monkeypatch.setattr(usage_service, "_claude_oauth", lambda: None)
    monkeypatch.setattr(usage_service, "_read_claude_keychain_credentials", lambda: None)
    monkeypatch.setattr(usage_service, "_claude_keychain_item_exists", lambda: True)
    monkeypatch.setattr(usage_service, "_scan_claude_logs", lambda *a, **k: [])
    monkeypatch.setattr(usage_service.claude_session_usage, "fetch_usage_body", lambda client: None)

    result = usage_service._fetch_claude_usage(_FakeHttpClient())

    assert result.logged_in is False
    assert "Keychain unreadable" in result.error
    assert "re-authenticate" not in result.error
    assert len(result.error) < 100


def test_usage_rate_limit_backoff_skips_live_fetch(tmp_path, monkeypatch):
    cache_path = tmp_path / "data" / usage_service.USAGE_CACHE_FILENAME
    monkeypatch.setattr(usage_service, "_usage_cache_path", lambda: cache_path)
    future = (datetime.now(tz=timezone.utc) + timedelta(minutes=10)).isoformat()
    cache_path.parent.mkdir(parents=True)
    cache_path.write_text(
        json.dumps({"claude": {"provider": "claude", "rate_limited_until": future}}),
        encoding="utf-8",
    )

    calls = {"count": 0}

    def fake_get(*args, **kwargs):
        calls["count"] += 1
        raise AssertionError("should not call Claude usage API during backoff")

    monkeypatch.setattr(usage_service, "_claude_oauth", lambda: {"accessToken": "token"})
    monkeypatch.setattr(
        usage_service,
        "_fetch_cursor_usage",
        lambda client, cache_entry=None: usage_service.ProviderUsage(
            provider="cursor", logged_in=False
        ),
    )

    with patch.object(usage_service.httpx.Client, "get", fake_get):
        snapshot = usage_service.get_usage_snapshot()

    assert calls["count"] == 0
    claude = next(p for p in snapshot["providers"] if p["provider"] == "claude")
    assert "backing off" in claude["error"].lower()


def test_cursor_rate_limit_backoff_skips_live_fetch(tmp_path, monkeypatch):
    cache_path = tmp_path / "data" / usage_service.USAGE_CACHE_FILENAME
    monkeypatch.setattr(usage_service, "_usage_cache_path", lambda: cache_path)
    future = (datetime.now(tz=timezone.utc) + timedelta(minutes=10)).isoformat()
    cache_path.parent.mkdir(parents=True)
    cache_path.write_text(
        json.dumps(
            {
                "cursor": {
                    "provider": "cursor",
                    "plan": "Pro",
                    "rate_limited_until": future,
                }
            }
        ),
        encoding="utf-8",
    )

    calls = {"count": 0}

    def fake_post(*args, **kwargs):
        calls["count"] += 1
        raise AssertionError("should not call Cursor usage API during backoff")

    monkeypatch.setattr(
        usage_service,
        "_fetch_claude_usage",
        lambda client, cache_entry=None: usage_service.ProviderUsage(
            provider="claude", logged_in=False
        ),
    )
    monkeypatch.setattr(usage_service, "_read_cursor_access_token", lambda: "cursor-token")

    with patch.object(usage_service.httpx.Client, "post", fake_post):
        snapshot = usage_service.get_usage_snapshot()

    assert calls["count"] == 0
    cursor = next(p for p in snapshot["providers"] if p["provider"] == "cursor")
    assert cursor["plan"] == "Pro"
    assert "backing off" in cursor["error"].lower()


def test_usage_cache_not_used_when_not_logged_in(tmp_path, monkeypatch):
    cache_path = tmp_path / "data" / usage_service.USAGE_CACHE_FILENAME
    monkeypatch.setattr(usage_service, "_usage_cache_path", lambda: cache_path)
    cache_path.parent.mkdir(parents=True)
    cache_path.write_text(
        json.dumps(
            {
                "cursor": {
                    "provider": "cursor",
                    "plan": "Ultra",
                    "logged_in": True,
                    "error": None,
                    "meters": [
                        {
                            "key": "total",
                            "label": "Total usage",
                            "used": 10.0,
                            "limit": 100.0,
                            "unit": "percent",
                            "percent_used": 10.0,
                            "resets_at": None,
                            "status": "ok",
                        }
                    ],
                    "breakdown": [],
                    "from_cache": False,
                    "cached_at": "2026-07-05T20:00:00+00:00",
                }
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(
        usage_service,
        "_fetch_claude_usage",
        lambda client, cache_entry=None: usage_service.ProviderUsage(
            provider="claude", logged_in=False, error="Not logged in."
        ),
    )
    monkeypatch.setattr(
        usage_service,
        "_fetch_cursor_usage",
        lambda client, cache_entry=None: usage_service.ProviderUsage(
            provider="cursor", logged_in=False, error="Not logged in to Cursor."
        ),
    )

    snapshot = usage_service.get_usage_snapshot()
    cursor = next(p for p in snapshot["providers"] if p["provider"] == "cursor")

    assert cursor["from_cache"] is False
    assert cursor["meters"] == []


# --- Claude transcript scanning ---------------------------------------------


def _transcript_line(*, timestamp: str, model: str, output_tokens: int) -> str:
    """One assistant row in the shape Claude Code actually writes.

    Token counts live under `message.usage`, not at the top level, and the
    timestamp is an ISO-8601 string rather than an epoch number.
    """
    return json.dumps(
        {
            "type": "assistant",
            "timestamp": timestamp,
            "message": {
                "model": model,
                "usage": {
                    "input_tokens": 10,
                    "output_tokens": output_tokens,
                    "cache_read_input_tokens": 0,
                    "cache_creation_input_tokens": 0,
                },
            },
        }
    )


def _write_transcript(claude_home, lines) -> None:
    project_dir = claude_home / "projects" / "-Users-someone-repo"
    project_dir.mkdir(parents=True, exist_ok=True)
    (project_dir / "session.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _recent(hours_ago: int) -> str:
    return (datetime.now(tz=timezone.utc) - timedelta(hours=hours_ago)).isoformat()


def test_scan_claude_logs_reads_usage_nested_under_message(tmp_path):
    """Regression: the scanner read `row["usage"]`, which Claude Code never
    writes, so the per-model breakdown was silently always empty."""
    _write_transcript(
        tmp_path,
        [
            _transcript_line(timestamp=_recent(1), model="claude-opus-5", output_tokens=90),
            _transcript_line(timestamp=_recent(2), model="claude-sonnet-5", output_tokens=40),
        ],
    )

    with patch.object(usage_service, "_claude_home", return_value=tmp_path):
        items = usage_service._scan_claude_logs()

    assert [item.name for item in items] == ["claude-opus-5", "claude-sonnet-5"]
    # 10 input + 90 output, and 10 + 40.
    assert [item.amount for item in items] == [100.0, 50.0]


def test_scan_claude_logs_still_reads_top_level_usage(tmp_path):
    """Other writers and older transcripts put usage at the top level; the fix
    must add the nested shape without dropping that one."""
    line = json.dumps(
        {
            "timestamp": _recent(1),
            "model": "claude-opus-5",
            "usage": {"input_tokens": 5, "output_tokens": 5},
        }
    )
    _write_transcript(tmp_path, [line])

    with patch.object(usage_service, "_claude_home", return_value=tmp_path):
        items = usage_service._scan_claude_logs()

    assert [(item.name, item.amount) for item in items] == [("claude-opus-5", 10.0)]


def test_scan_claude_logs_applies_the_days_back_window(tmp_path):
    """Regression: ISO timestamps were parsed as numbers, yielding None, so the
    window never excluded anything and every row ever written was counted."""
    _write_transcript(
        tmp_path,
        [
            _transcript_line(timestamp=_recent(1), model="claude-opus-5", output_tokens=90),
            _transcript_line(timestamp=_recent(24 * 30), model="claude-opus-5", output_tokens=990),
        ],
    )

    with patch.object(usage_service, "_claude_home", return_value=tmp_path):
        items = usage_service._scan_claude_logs(days_back=7)

    assert [(item.name, item.amount) for item in items] == [("claude-opus-5", 100.0)]


def test_scan_claude_logs_shares_sum_to_one_hundred(tmp_path):
    _write_transcript(
        tmp_path,
        [
            _transcript_line(timestamp=_recent(1), model="claude-opus-5", output_tokens=140),
            _transcript_line(timestamp=_recent(1), model="claude-sonnet-5", output_tokens=40),
        ],
    )

    with patch.object(usage_service, "_claude_home", return_value=tmp_path):
        items = usage_service._scan_claude_logs()

    assert round(sum(item.share_percent for item in items), 1) == 100.0


# --- Session-key fallback ----------------------------------------------------


_SESSION_BODY = {
    "five_hour": {"utilization": 33.0, "resets_at": "2026-08-03T12:00:00Z"},
    "seven_day": {"utilization": 12.0, "resets_at": "2026-08-09T12:00:00Z"},
}


def _oauth_failure(error: str = "Not logged in. Run `claude` to authenticate."):
    return usage_service.ProviderUsage(provider="claude", logged_in=False, error=error)


def test_session_key_supplies_meters_when_oauth_has_none():
    with (
        patch.object(usage_service, "_fetch_claude_usage_oauth", return_value=_oauth_failure()),
        patch.object(usage_service, "_scan_claude_logs", return_value=[]),
        patch.object(
            usage_service.claude_session_usage, "fetch_usage_body", return_value=_SESSION_BODY
        ),
    ):
        provider = usage_service._fetch_claude_usage(_FakeHttpClient())

    assert provider.error is None
    assert provider.logged_in is True
    assert [(m.key, m.used) for m in provider.meters] == [
        ("five_hour", 33.0),
        ("seven_day", 12.0),
    ]


def test_oauth_result_wins_when_it_already_has_meters():
    """The official route is preferred; the fallback must not fire behind it."""
    oauth_ok = usage_service.ProviderUsage(
        provider="claude",
        logged_in=True,
        meters=[
            usage_service.UsageMeter(
                key="five_hour", label="Session (5h)", used=7.0, limit=100.0, unit="percent"
            )
        ],
    )

    with (
        patch.object(usage_service, "_fetch_claude_usage_oauth", return_value=oauth_ok),
        patch.object(usage_service.claude_session_usage, "fetch_usage_body") as fetch_body,
    ):
        provider = usage_service._fetch_claude_usage(_FakeHttpClient())

    fetch_body.assert_not_called()
    assert provider.meters[0].used == 7.0


def test_oauth_error_is_kept_when_session_key_is_not_configured():
    with (
        patch.object(
            usage_service,
            "_fetch_claude_usage_oauth",
            return_value=_oauth_failure("Claude Code session is logged out — run `claude /login`."),
        ),
        patch.object(usage_service, "_scan_claude_logs", return_value=[]),
        patch.object(usage_service.claude_session_usage, "fetch_usage_body", return_value=None),
    ):
        provider = usage_service._fetch_claude_usage(_FakeHttpClient())

    assert provider.error == "Claude Code session is logged out — run `claude /login`."
    assert provider.meters == []


def test_session_key_failure_is_surfaced_over_the_oauth_error():
    """A user who deliberately configured the fallback needs to see why it is
    failing; the OAuth message would just send them back to a dead end."""
    unavailable = usage_service.claude_session_usage.SessionUsageUnavailable(
        "claude.ai blocked the request (Cloudflare challenge)."
    )

    with (
        patch.object(usage_service, "_fetch_claude_usage_oauth", return_value=_oauth_failure()),
        patch.object(usage_service, "_scan_claude_logs", return_value=[]),
        patch.object(
            usage_service.claude_session_usage, "fetch_usage_body", side_effect=unavailable
        ),
    ):
        provider = usage_service._fetch_claude_usage(_FakeHttpClient())

    assert provider.error == "claude.ai blocked the request (Cloudflare challenge)."


def test_session_key_fallback_preserves_rate_limit_backoff():
    """Succeeding against claude.ai must not clear the Anthropic-side backoff,
    or the next poll would hammer an endpoint that is still rate limiting."""
    backing_off = usage_service.ProviderUsage(
        provider="claude",
        logged_in=True,
        error="Claude usage API rate limited — backing off.",
        rate_limited_until="2026-08-03T13:00:00+00:00",
        rate_limit_streak=2,
    )

    with (
        patch.object(usage_service, "_fetch_claude_usage_oauth", return_value=backing_off),
        patch.object(usage_service, "_scan_claude_logs", return_value=[]),
        patch.object(
            usage_service.claude_session_usage, "fetch_usage_body", return_value=_SESSION_BODY
        ),
    ):
        provider = usage_service._fetch_claude_usage(_FakeHttpClient())

    assert provider.meters
    assert provider.rate_limited_until == "2026-08-03T13:00:00+00:00"
    assert provider.rate_limit_streak == 2


def test_network_error_in_fallback_leaves_the_oauth_error_intact():
    with (
        patch.object(usage_service, "_fetch_claude_usage_oauth", return_value=_oauth_failure()),
        patch.object(usage_service, "_scan_claude_logs", return_value=[]),
        patch.object(
            usage_service.claude_session_usage,
            "fetch_usage_body",
            side_effect=httpx.ConnectError("no route"),
        ),
    ):
        provider = usage_service._fetch_claude_usage(_FakeHttpClient())

    assert provider.error == "Not logged in. Run `claude` to authenticate."


def test_empty_meters_do_not_overwrite_the_cached_readings(tmp_path, monkeypatch):
    """An error-free response carrying no meters means the payload shape moved,
    not that usage is zero — caching it would destroy the last good readings."""
    cache_path = tmp_path / "data" / usage_service.USAGE_CACHE_FILENAME
    monkeypatch.setattr(usage_service, "_usage_cache_path", lambda: cache_path)
    cache_path.parent.mkdir(parents=True)
    cache_path.write_text(
        json.dumps(
            {
                "claude": {
                    "provider": "claude",
                    "logged_in": True,
                    "error": None,
                    "meters": [
                        {
                            "key": "five_hour",
                            "label": "Session (5h)",
                            "used": 55.0,
                            "limit": 100.0,
                            "unit": "percent",
                            "percent_used": 55.0,
                            "resets_at": None,
                            "status": "ok",
                        }
                    ],
                    "breakdown": [],
                    "cached_at": "2026-07-05T20:00:00+00:00",
                }
            }
        ),
        encoding="utf-8",
    )

    shape_drift = usage_service.ProviderUsage(provider="claude", logged_in=True, error=None)
    cursor = usage_service.ProviderUsage(provider="cursor", logged_in=False, error="No Cursor.")

    monkeypatch.setattr(
        usage_service, "_fetch_claude_usage", lambda client, cache_entry=None: shape_drift
    )
    monkeypatch.setattr(
        usage_service, "_fetch_cursor_usage", lambda client, cache_entry=None: cursor
    )

    snapshot = usage_service.get_usage_snapshot()

    cached = json.loads(cache_path.read_text(encoding="utf-8"))
    assert cached["claude"]["meters"][0]["used"] == 55.0
    claude = next(p for p in snapshot["providers"] if p["provider"] == "claude")
    assert claude["from_cache"] is True


def test_scan_claude_logs_skips_transcripts_older_than_the_window(tmp_path):
    """Reading every transcript ever written cost ~13s per poll against a 1.4 GB
    tree. A file last written before the window can only hold rows outside it."""
    project_dir = tmp_path / "projects" / "-Users-someone-repo"
    project_dir.mkdir(parents=True)

    fresh = project_dir / "fresh.jsonl"
    fresh.write_text(
        _transcript_line(timestamp=_recent(1), model="claude-opus-5", output_tokens=90) + "\n",
        encoding="utf-8",
    )
    stale = project_dir / "stale.jsonl"
    stale.write_text(
        # An in-window timestamp inside a long-untouched file: only the mtime
        # check can exclude it, so this fails if the skip regresses.
        _transcript_line(timestamp=_recent(1), model="claude-sonnet-5", output_tokens=990) + "\n",
        encoding="utf-8",
    )
    stale_mtime = (datetime.now(tz=timezone.utc) - timedelta(days=30)).timestamp()
    os.utime(stale, (stale_mtime, stale_mtime))

    with patch.object(usage_service, "_claude_home", return_value=tmp_path):
        items = usage_service._scan_claude_logs(days_back=7)

    assert [(item.name, item.amount) for item in items] == [("claude-opus-5", 100.0)]


def test_breakdown_survives_a_missing_credential(monkeypatch):
    """The breakdown is parsed from local transcripts, so a logged-out account
    still gets one — the no-credential branch used to drop it on the floor."""
    item = usage_service.UsageBreakdownItem(
        name="claude-opus-5", amount=100.0, unit="tokens", share_percent=100.0
    )
    monkeypatch.setattr(usage_service, "_claude_oauth", lambda: None)
    monkeypatch.setattr(usage_service, "_read_claude_keychain_credentials", lambda: None)
    monkeypatch.setattr(usage_service, "_claude_keychain_item_exists", lambda: False)
    monkeypatch.setattr(usage_service, "_scan_claude_logs", lambda *a, **k: [item])
    monkeypatch.setattr(usage_service.claude_session_usage, "fetch_usage_body", lambda client: None)

    result = usage_service._fetch_claude_usage(_FakeHttpClient())

    assert result.logged_in is False
    assert [b.name for b in result.breakdown] == ["claude-opus-5"]
