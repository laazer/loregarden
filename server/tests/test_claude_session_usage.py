"""Session-key fallback for reading Claude usage from claude.ai."""

from unittest.mock import MagicMock, patch

import httpx
import pytest
from loregarden.services import claude_session_usage


def _response(url: str, status_code: int = 200, *, json=None, text=None, headers=None):
    request = httpx.Request("GET", url)
    if json is not None:
        return httpx.Response(status_code, json=json, request=request)
    return httpx.Response(status_code, text=text or "", headers=headers or {}, request=request)


def _client(*responses):
    client = MagicMock(spec=httpx.Client)
    client.get.side_effect = list(responses)
    return client


@pytest.fixture(autouse=True)
def _clear_org_cache():
    claude_session_usage.clear_org_cache()
    yield
    claude_session_usage.clear_org_cache()


@pytest.fixture
def _no_env(monkeypatch):
    monkeypatch.delenv(claude_session_usage.SESSION_KEY_ENV, raising=False)
    monkeypatch.delenv(claude_session_usage.ORG_UUID_ENV, raising=False)


def test_session_key_read_from_env_wins_over_file(tmp_path, monkeypatch):
    key_path = tmp_path / "data" / claude_session_usage.SESSION_KEY_FILENAME
    key_path.parent.mkdir(parents=True)
    key_path.write_text("from-file", encoding="utf-8")
    monkeypatch.setattr(claude_session_usage, "session_key_file_path", lambda: key_path)
    monkeypatch.setenv(claude_session_usage.SESSION_KEY_ENV, "from-env")

    assert claude_session_usage.read_session_key() == "from-env"


def test_session_key_read_from_file_and_stripped(tmp_path, monkeypatch, _no_env):
    key_path = tmp_path / "data" / claude_session_usage.SESSION_KEY_FILENAME
    key_path.parent.mkdir(parents=True)
    key_path.write_text("  sk-ant-sid01-abc123  \n", encoding="utf-8")
    monkeypatch.setattr(claude_session_usage, "session_key_file_path", lambda: key_path)

    assert claude_session_usage.read_session_key() == "sk-ant-sid01-abc123"


def test_session_key_absent_when_file_missing(tmp_path, monkeypatch, _no_env):
    monkeypatch.setattr(claude_session_usage, "session_key_file_path", lambda: tmp_path / "nope")

    assert claude_session_usage.read_session_key() is None


def test_session_key_rejects_pasted_cookie_line(tmp_path, monkeypatch, _no_env):
    """A cookie goes into a header verbatim — whitespace would break encoding at
    request time, far from the paste that caused it."""
    key_path = tmp_path / "data" / claude_session_usage.SESSION_KEY_FILENAME
    key_path.parent.mkdir(parents=True)
    key_path.write_text("sessionKey=sk-ant-sid01-abc; Path=/", encoding="utf-8")
    monkeypatch.setattr(claude_session_usage, "session_key_file_path", lambda: key_path)

    assert claude_session_usage.read_session_key() is None


def test_fetch_usage_body_returns_none_when_unconfigured():
    client = MagicMock(spec=httpx.Client)
    with patch.object(claude_session_usage, "read_session_key", return_value=None):
        assert claude_session_usage.fetch_usage_body(client) is None
    client.get.assert_not_called()


def test_fetch_usage_body_resolves_org_and_returns_payload(_no_env):
    orgs = [
        {"uuid": "org-no-chat", "name": "Other", "capabilities": ["api"]},
        {"uuid": "org-chat", "name": "Personal", "capabilities": ["chat", "claude_pro"]},
    ]
    usage = {"five_hour": {"utilization": 12.5, "resets_at": "2026-08-03T10:00:00Z"}}
    client = _client(
        _response(claude_session_usage.CLAUDE_ORGS_URL, json=orgs),
        _response(f"{claude_session_usage.CLAUDE_ORGS_URL}/org-chat/usage", json=usage),
    )

    with patch.object(claude_session_usage, "read_session_key", return_value="key-1"):
        body = claude_session_usage.fetch_usage_body(client)

    assert body == usage
    usage_call = client.get.call_args_list[1]
    assert usage_call.args[0].endswith("/organizations/org-chat/usage")


def test_session_key_travels_as_a_cookie_never_a_bearer_token(_no_env):
    client = _client(
        _response(claude_session_usage.CLAUDE_ORGS_URL, json=[{"uuid": "o", "capabilities": []}]),
        _response(f"{claude_session_usage.CLAUDE_ORGS_URL}/o/usage", json={}),
    )

    with patch.object(claude_session_usage, "read_session_key", return_value="secret-key"):
        claude_session_usage.fetch_usage_body(client)

    headers = client.get.call_args_list[0].kwargs["headers"]
    assert headers["Cookie"] == "sessionKey=secret-key"
    assert "Authorization" not in headers


def test_org_uuid_resolved_once_per_session_key(_no_env):
    orgs = [{"uuid": "org-1", "capabilities": ["chat"]}]
    client = _client(
        _response(claude_session_usage.CLAUDE_ORGS_URL, json=orgs),
        _response(f"{claude_session_usage.CLAUDE_ORGS_URL}/org-1/usage", json={"five_hour": {}}),
        _response(f"{claude_session_usage.CLAUDE_ORGS_URL}/org-1/usage", json={"five_hour": {}}),
    )

    with patch.object(claude_session_usage, "read_session_key", return_value="key-1"):
        claude_session_usage.fetch_usage_body(client)
        claude_session_usage.fetch_usage_body(client)

    # Three calls total, not four: the organization lookup happened once.
    assert client.get.call_count == 3


def test_org_uuid_env_override_skips_lookup(monkeypatch, _no_env):
    monkeypatch.setenv(claude_session_usage.ORG_UUID_ENV, "forced-org")
    client = _client(
        _response(f"{claude_session_usage.CLAUDE_ORGS_URL}/forced-org/usage", json={"a": 1})
    )

    with patch.object(claude_session_usage, "read_session_key", return_value="key-1"):
        claude_session_usage.fetch_usage_body(client)

    assert client.get.call_count == 1
    assert client.get.call_args.args[0].endswith("/organizations/forced-org/usage")


def test_cloudflare_challenge_is_not_reported_as_expired_session(_no_env):
    """An HTML interstitial and a rejected cookie both arrive as 403 but need
    different fixes, so they must not share an error message."""
    client = _client(
        _response(
            claude_session_usage.CLAUDE_ORGS_URL,
            403,
            text="<!DOCTYPE html><html><body>Just a moment...</body></html>",
            headers={"content-type": "text/html; charset=utf-8"},
        )
    )

    with patch.object(claude_session_usage, "read_session_key", return_value="key-1"):
        with pytest.raises(claude_session_usage.SessionUsageUnavailable) as excinfo:
            claude_session_usage.fetch_usage_body(client)

    assert "Cloudflare" in excinfo.value.message


def test_rejected_cookie_reports_expiry(_no_env):
    client = _client(
        _response(claude_session_usage.CLAUDE_ORGS_URL, json=[{"uuid": "o", "capabilities": []}]),
        _response(
            f"{claude_session_usage.CLAUDE_ORGS_URL}/o/usage",
            401,
            json={"error": {"type": "authentication_error"}},
        ),
    )

    with patch.object(claude_session_usage, "read_session_key", return_value="key-1"):
        with pytest.raises(claude_session_usage.SessionUsageUnavailable) as excinfo:
            claude_session_usage.fetch_usage_body(client)

    assert "expired" in excinfo.value.message.lower()


def test_error_envelope_on_a_200_is_treated_as_expiry(_no_env):
    """A stale cookie can answer 200 with an error body rather than a 401."""
    client = _client(
        _response(claude_session_usage.CLAUDE_ORGS_URL, json=[{"uuid": "o", "capabilities": []}]),
        _response(
            f"{claude_session_usage.CLAUDE_ORGS_URL}/o/usage",
            json={"error": {"type": "permission_error", "message": "nope"}},
        ),
    )

    with patch.object(claude_session_usage, "read_session_key", return_value="key-1"):
        with pytest.raises(claude_session_usage.SessionUsageUnavailable):
            claude_session_usage.fetch_usage_body(client)


def test_session_error_messages_fit_the_ui_slot(_no_env):
    """These render in a ~240px slot beside the provider title, same as the
    OAuth-side diagnosis (.usage-provider-error in client/src/index.css)."""
    client = _client(_response(claude_session_usage.CLAUDE_ORGS_URL, 500, text="boom"))

    with patch.object(claude_session_usage, "read_session_key", return_value="key-1"):
        with pytest.raises(claude_session_usage.SessionUsageUnavailable) as excinfo:
            claude_session_usage.fetch_usage_body(client)

    assert len(excinfo.value.message) < 100
