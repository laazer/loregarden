from fastapi.testclient import TestClient


def test_health_includes_mcp(client: TestClient):
    res = client.get("/health")
    assert res.status_code == 200
    assert res.json()["mcp"] == "/mcp"


def test_mcp_initialize(client: TestClient):
    res = client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "test", "version": "0"},
            },
        },
    )
    assert res.status_code == 200
    body = res.json()
    assert body["result"]["serverInfo"]["name"] == "loregarden"


def test_mcp_tools_list(client: TestClient):
    res = client.post(
        "/mcp",
        json={"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
    )
    assert res.status_code == 200
    tools = res.json()["result"]["tools"]
    names = {t["name"] for t in tools}
    assert "loregarden_get_ticket" in names
    assert "loregarden_complete_stage" in names


def test_mcp_get_ticket_by_external(client: TestClient):
    res = client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {
                "name": "loregarden_get_ticket_by_external",
                "arguments": {
                    "workspace_slug": "loregarden",
                    "external_id": "01-bootstrap-fastapi-control-plane",
                },
            },
        },
    )
    assert res.status_code == 200
    body = res.json()
    assert "result" in body
    text = body["result"]["content"][0]["text"]
    assert "01-bootstrap-fastapi-control-plane" in text
