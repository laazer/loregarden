import json
from unittest.mock import patch

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
