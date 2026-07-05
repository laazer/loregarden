import json

from fastapi.testclient import TestClient
from sqlmodel import Session, select

from loregarden.models.domain import Ticket
from loregarden.services.orchestration_callbacks import OrchestrationCallbackService
from loregarden.services.ticket_discovery import list_tickets_mcp, looks_like_ticket_uuid


def test_looks_like_ticket_uuid():
    assert looks_like_ticket_uuid("6b47736f-5afb-477b-af40-b00b8de68a44") is True
    assert looks_like_ticket_uuid("03-wire-cli-agent-runner") is False


def test_resolve_ticket_accepts_external_slug_as_ticket_id(client: TestClient):
    from loregarden.db.session import engine

    with Session(engine) as session:
        svc = OrchestrationCallbackService(session)
        ticket = svc.resolve_ticket(
            ticket_id="01-bootstrap-fastapi-control-plane",
            workspace_slug="loregarden",
        )
        assert ticket.external_id == "01-bootstrap-fastapi-control-plane"


def test_list_tickets_mcp_search(client: TestClient):
    from loregarden.db.session import engine

    with Session(engine) as session:
        payload = list_tickets_mcp(session, workspace_slug="loregarden", search="cli")
        assert payload["count"] >= 1
        assert any("cli" in row["external_id"] for row in payload["tickets"])


def test_mcp_list_tickets_tool(client: TestClient):
    res = client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 10,
            "method": "tools/call",
            "params": {
                "name": "loregarden_list_tickets",
                "arguments": {
                    "workspace_slug": "loregarden",
                    "search": "bootstrap",
                },
            },
        },
    )
    assert res.status_code == 200
    text = res.json()["result"]["content"][0]["text"]
    payload = json.loads(text)
    assert payload["count"] >= 1
    assert payload["tickets"][0]["external_id"]


def test_mcp_get_ticket_by_slug(client: TestClient):
    res = client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 11,
            "method": "tools/call",
            "params": {
                "name": "loregarden_get_ticket",
                "arguments": {
                    "ticket_id": "03-wire-cli-agent-runner",
                    "workspace_slug": "loregarden",
                },
            },
        },
    )
    assert res.status_code == 200
    payload = json.loads(res.json()["result"]["content"][0]["text"])
    assert payload["external_id"] == "03-wire-cli-agent-runner"
    assert "hierarchy" in payload
    assert payload["hierarchy"]["self"]["external_id"] == "03-wire-cli-agent-runner"


def test_mcp_get_ticket_includes_hierarchy_children(client: TestClient):
    from loregarden.db.session import engine

    with Session(engine) as session:
        parent = session.exec(
            select(Ticket).where(Ticket.external_id == "m01-backend-platform")
        ).first()
        assert parent is not None
        child_count = len(
            session.exec(select(Ticket).where(Ticket.parent_ticket_id == parent.id)).all()
        )
        assert child_count >= 1

    res = client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 12,
            "method": "tools/call",
            "params": {
                "name": "loregarden_get_ticket",
                "arguments": {
                    "ticket_id": "m01-backend-platform",
                    "workspace_slug": "loregarden",
                },
            },
        },
    )
    assert res.status_code == 200
    payload = json.loads(res.json()["result"]["content"][0]["text"])
    assert len(payload["hierarchy"]["children"]) >= 1
