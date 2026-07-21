from unittest.mock import patch

import pytest
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.testclient import TestClient
from loregarden.core.auth import TokenAuthMiddleware, websocket_token_ok


def _build_app(token: str) -> FastAPI:
    app = FastAPI()
    app.add_middleware(TokenAuthMiddleware, token=token)

    @app.get("/health")
    def health():
        return {"status": "ok"}

    @app.get("/api/ping")
    def ping():
        return {"pong": True}

    @app.post("/api/ping")
    def ping_post():
        return {"pong": True}

    return app


def test_no_token_configured_is_passthrough():
    client = TestClient(_build_app(""))
    assert client.get("/api/ping").status_code == 200
    assert client.post("/api/ping").status_code == 200


def test_configured_token_rejects_missing_credentials():
    client = TestClient(_build_app("s3cret"))
    res = client.get("/api/ping")
    assert res.status_code == 401
    assert res.json()["detail"] == "Missing or invalid API token"


def test_configured_token_rejects_wrong_credentials():
    client = TestClient(_build_app("s3cret"))
    assert client.get("/api/ping", headers={"Authorization": "Bearer nope"}).status_code == 401
    assert client.get("/api/ping", headers={"X-Loregarden-Token": "nope"}).status_code == 401


def test_bearer_token_accepted():
    client = TestClient(_build_app("s3cret"))
    res = client.get("/api/ping", headers={"Authorization": "Bearer s3cret"})
    assert res.status_code == 200
    assert res.json() == {"pong": True}


def test_x_header_token_accepted():
    client = TestClient(_build_app("s3cret"))
    res = client.post("/api/ping", headers={"X-Loregarden-Token": "s3cret"})
    assert res.status_code == 200


def test_health_is_exempt_even_with_token():
    client = TestClient(_build_app("s3cret"))
    assert client.get("/health").status_code == 200


def _build_websocket_app() -> FastAPI:
    """A websocket behind the same middleware the real app uses.

    Driven through a real handshake rather than a mocked WebSocket: the whole
    point of `websocket_token_ok` is that the middleware does not run for this
    scope, and only an actual connection proves that.
    """
    app = FastAPI()
    app.add_middleware(TokenAuthMiddleware, token="s3cret")

    @app.websocket("/ws")
    async def endpoint(websocket: WebSocket) -> None:
        if not websocket_token_ok(websocket):
            await websocket.close(code=1008)
            return
        await websocket.accept()
        await websocket.send_text("ok")

    return app


def test_middleware_does_not_authenticate_websockets():
    """BaseHTTPMiddleware only sees HTTP scopes. If this ever starts failing,
    Starlette changed and the per-endpoint checks can be reconsidered — until
    then every websocket endpoint must check the token itself."""
    client = TestClient(_build_websocket_app())

    with patch("loregarden.core.auth.settings") as cfg:
        cfg.api_token = ""
        # No token presented, and the middleware is configured with one — yet
        # the connection succeeds, because the middleware never runs here.
        with client.websocket_connect("/ws") as socket:
            assert socket.receive_text() == "ok"


def test_websocket_token_accepted_as_query_parameter():
    """Browsers cannot set headers on `new WebSocket(...)`, so the query
    parameter is the only way a page can present a token."""
    client = TestClient(_build_websocket_app())

    with patch("loregarden.core.auth.settings") as cfg:
        cfg.api_token = "s3cret"
        with client.websocket_connect("/ws?token=s3cret") as socket:
            assert socket.receive_text() == "ok"


def test_websocket_token_accepted_as_header():
    client = TestClient(_build_websocket_app())

    with patch("loregarden.core.auth.settings") as cfg:
        cfg.api_token = "s3cret"
        with client.websocket_connect("/ws", headers={"X-Loregarden-Token": "s3cret"}) as socket:
            assert socket.receive_text() == "ok"


def test_websocket_with_wrong_token_is_refused():
    client = TestClient(_build_websocket_app())

    with patch("loregarden.core.auth.settings") as cfg:
        cfg.api_token = "s3cret"
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect("/ws?token=nope") as socket:
                socket.receive_text()


def test_websocket_without_a_configured_token_is_open():
    """The rest of the API is already reachable by any local process; refusing
    only websockets would be theatre."""
    client = TestClient(_build_websocket_app())

    with patch("loregarden.core.auth.settings") as cfg:
        cfg.api_token = ""
        with client.websocket_connect("/ws") as socket:
            assert socket.receive_text() == "ok"
