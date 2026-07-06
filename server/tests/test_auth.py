from fastapi import FastAPI
from fastapi.testclient import TestClient
from loregarden.core.auth import TokenAuthMiddleware


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
