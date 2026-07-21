"""Whether a registered MCP server actually answers."""

import json
from unittest.mock import patch

import httpx
from loregarden.models.domain import McpServer
from loregarden.services.mcp_health import (
    HealthResult,
    check_server,
    record_health,
)
from sqlmodel import Session


def _http_server(**overrides) -> McpServer:
    return McpServer(name="github", transport="http", url="https://mcp.example/sse", **overrides)


def _initialize_response(body: str, status: int = 200) -> httpx.Response:
    return httpx.Response(status_code=status, text=body)


def _ok_body() -> str:
    return json.dumps(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "result": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "serverInfo": {"name": "github-mcp"},
            },
        }
    )


def test_a_real_handshake_is_healthy():
    with patch("httpx.post", return_value=_initialize_response(_ok_body())):
        result = check_server(_http_server())

    assert result.ok
    assert result.error == ""
    assert result.server_name == "github-mcp"


def test_a_200_from_something_that_is_not_mcp_is_not_healthy():
    """The reason this checks a handshake rather than reachability.

    A login page, a proxy error page, or any endpoint that answers 200 would
    pass a ping and fail the moment an agent tried to use it.
    """
    with patch("httpx.post", return_value=_initialize_response('{"hello": "world"}')):
        result = check_server(_http_server())

    assert not result.ok
    assert "initialize" in result.error


def test_a_json_rpc_error_is_not_healthy():
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "error": {"message": "unsupported version"}})
    with patch("httpx.post", return_value=_initialize_response(body)):
        result = check_server(_http_server())

    assert not result.ok
    # The server's own words, which is what the operator has to act on.
    assert result.error == "unsupported version"


def test_an_sse_framed_answer_is_read():
    """Streamable-HTTP servers reply in `data:` frames, not bare JSON."""
    body = f"event: message\ndata: {_ok_body()}\n\n"
    with patch("httpx.post", return_value=_initialize_response(body)):
        result = check_server(_http_server())

    assert result.ok


def test_an_http_error_status_reports_the_status():
    with patch("httpx.post", return_value=_initialize_response("nope", status=502)):
        result = check_server(_http_server())

    assert not result.ok
    assert result.error == "HTTP 502"


def test_a_timeout_says_so_rather_than_hanging():
    with patch("httpx.post", side_effect=httpx.TimeoutException("slow")):
        result = check_server(_http_server())

    assert not result.ok
    assert "within" in result.error


def test_a_missing_credential_is_reported_before_dialling():
    """Naming the unset variable is the whole point — "connection refused"
    would send the operator looking at the wrong thing."""
    server = _http_server(auth_env_var="GITHUB_MCP_TOKEN")
    with patch.dict("os.environ", {}, clear=True), patch("httpx.post") as posted:
        result = check_server(server)

    assert not result.ok
    assert "GITHUB_MCP_TOKEN" in result.error
    posted.assert_not_called()


def test_a_check_that_raises_is_a_failed_check_not_a_crash():
    with patch("httpx.post", side_effect=RuntimeError("boom")):
        result = check_server(_http_server())

    assert not result.ok
    assert "boom" in result.error


def test_a_missing_stdio_command_names_the_command():
    server = McpServer(name="local", transport="stdio", command="definitely-not-installed-xyz")
    result = check_server(server)

    assert not result.ok
    assert "definitely-not-installed-xyz" in result.error


def test_recording_a_check_does_not_look_like_an_edit(db_session: Session):
    """A check observes the server; it does not change how it is configured.

    Bumping updated_at would make every check indistinguishable from an
    operator editing the registration.
    """
    server = McpServer(name="github", transport="http", url="https://mcp.example/sse")
    db_session.add(server)
    db_session.commit()
    db_session.refresh(server)
    before = server.updated_at

    record_health(db_session, server, HealthResult(ok=True, latency_ms=42, error=""))

    assert server.updated_at == before
    assert server.last_health_ok is True
    assert server.last_health_latency_ms == 42
    assert server.last_checked_at != ""


def test_a_failure_keeps_the_reason(db_session: Session):
    server = McpServer(name="broken", transport="http", url="https://mcp.example/sse")
    db_session.add(server)
    db_session.commit()

    record_health(db_session, server, HealthResult(ok=False, latency_ms=8000, error="HTTP 502"))

    assert server.last_health_ok is False
    assert server.last_health_error == "HTTP 502"


def test_the_endpoint_checks_and_returns_the_updated_server(client, db_session: Session):
    server = McpServer(name="github", transport="http", url="https://mcp.example/sse")
    db_session.add(server)
    db_session.commit()
    db_session.refresh(server)

    with patch("httpx.post", return_value=_initialize_response(_ok_body())):
        response = client.post(f"/api/mcp-servers/{server.id}/health-check")

    assert response.status_code == 200
    body = response.json()
    assert body["last_health_ok"] is True
    assert body["last_checked_at"] != ""


def test_checking_an_unknown_server_is_404(client):
    assert client.post("/api/mcp-servers/nope/health-check").status_code == 404
