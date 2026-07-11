import json
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


def test_claude_login_diagnosis_points_at_setup_token(monkeypatch):
    """Kept short — it renders in a 240px-wide UI slot next to the provider
    title (see .usage-provider-error in client/src/index.css), not a full
    details panel."""
    monkeypatch.setattr(usage_service, "_claude_keychain_item_exists", lambda: True)

    message = usage_service._claude_login_diagnosis()

    assert "claude:setup-token" in message
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
    monkeypatch.setattr(usage_service, "_claude_keychain_item_exists", lambda: False)

    with httpx.Client() as client:
        result = usage_service._fetch_claude_usage(client)

    assert result.logged_in is False
    assert result.error == "Not logged in. Run `claude` to authenticate."


def test_fetch_claude_usage_reports_keychain_access_issue_when_item_unreadable(monkeypatch):
    monkeypatch.setattr(usage_service, "_claude_oauth", lambda: None)
    monkeypatch.setattr(usage_service, "_claude_keychain_item_exists", lambda: True)

    with httpx.Client() as client:
        result = usage_service._fetch_claude_usage(client)

    assert result.logged_in is False
    assert "Keychain unreadable" in result.error
    assert "claude:setup-token" in result.error
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
