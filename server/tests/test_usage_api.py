from unittest.mock import patch

from fastapi.testclient import TestClient
from loregarden.services import usage_service


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
