from fastapi.testclient import TestClient


def _ticket_id(client: TestClient) -> str:
    tickets = client.get("/api/tickets").json()
    return tickets[0]["id"]


def test_ticket_detail_includes_default_orchestration_runtime(client: TestClient):
    ticket_id = _ticket_id(client)
    body = client.get(f"/api/tickets/{ticket_id}").json()
    assert body["orchestration_runtime"] == {
        "cli_adapter": "default",
        "claude_model": "",
        "cursor_model": "",
        "lmstudio_base_url": "",
        "lmstudio_model": "",
    }


def test_ticket_runtime_persists(client: TestClient):
    ticket_id = _ticket_id(client)
    res = client.patch(
        f"/api/tickets/{ticket_id}/runtime",
        json={
            "cli_adapter": "claude",
            "claude_model": "opus",
            "cursor_model": "",
            "lmstudio_base_url": "",
            "lmstudio_model": "",
        },
    )
    assert res.status_code == 200
    body = res.json()
    assert body["cli_adapter"] == "claude"
    assert body["claude_model"] == "opus"

    detail = client.get(f"/api/tickets/{ticket_id}").json()
    assert detail["orchestration_runtime"]["cli_adapter"] == "claude"
    assert detail["orchestration_runtime"]["claude_model"] == "opus"


def test_ticket_runtime_rejects_invalid_adapter(client: TestClient):
    ticket_id = _ticket_id(client)
    res = client.patch(
        f"/api/tickets/{ticket_id}/runtime",
        json={
            "cli_adapter": "not-valid",
            "claude_model": "",
            "cursor_model": "",
            "lmstudio_base_url": "",
            "lmstudio_model": "",
        },
    )
    assert res.status_code == 400


def test_ticket_runtime_is_independent_of_triage_runtime(client: TestClient):
    ticket_id = _ticket_id(client)
    client.patch(
        f"/api/tickets/{ticket_id}/triage/runtime",
        json={
            "cli_adapter": "cursor",
            "claude_model": "",
            "cursor_model": "sonnet-4",
            "lmstudio_base_url": "",
            "lmstudio_model": "",
        },
    )
    detail = client.get(f"/api/tickets/{ticket_id}").json()
    assert detail["orchestration_runtime"]["cli_adapter"] == "default"
    assert detail["orchestration_runtime"]["cursor_model"] == ""
