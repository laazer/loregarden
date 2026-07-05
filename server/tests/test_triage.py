import json

from fastapi.testclient import TestClient
from sqlmodel import Session, select

from loregarden.models.domain import Ticket, TriageMessage, Workspace


def _ticket_id(client: TestClient, *, external_id: str | None = None) -> str:
    tickets = client.get("/api/tickets").json()
    if external_id:
        match = next((t for t in tickets if t["external_id"] == external_id), None)
        if match:
            return match["id"]
    return tickets[0]["id"]


def test_triage_snapshot_empty(client: TestClient):
    ticket_id = _ticket_id(client, external_id="01-bootstrap-fastapi-control-plane")
    res = client.get(f"/api/tickets/{ticket_id}/triage")
    assert res.status_code == 200
    body = res.json()
    assert body["pending_approvals"] == []
    assert body["messages"] == []


def test_triage_snapshot_empty(client: TestClient):
    ticket_id = _ticket_id(client, external_id="01-bootstrap-fastapi-control-plane")
    res = client.get(f"/api/tickets/{ticket_id}/triage")
    assert res.status_code == 200
    body = res.json()
    assert body["pending_approvals"] == []
    assert body["messages"] == []
    assert body["runtime"]["cli_adapter"] == "default"


def test_triage_runtime_persists(client: TestClient):
    ticket_id = _ticket_id(client)
    res = client.patch(
        f"/api/tickets/{ticket_id}/triage/runtime",
        json={
            "cli_adapter": "lmstudio",
            "claude_model": "",
            "cursor_model": "",
            "lmstudio_base_url": "http://127.0.0.1:1234/v1",
            "lmstudio_model": "test-model",
        },
    )
    assert res.status_code == 200
    body = res.json()
    assert body["cli_adapter"] == "lmstudio"
    assert body["lmstudio_model"] == "test-model"

    snapshot = client.get(f"/api/tickets/{ticket_id}/triage").json()
    assert snapshot["runtime"]["cli_adapter"] == "lmstudio"
    assert snapshot["runtime"]["lmstudio_model"] == "test-model"


def test_triage_runtime_rejects_invalid_adapter(client: TestClient):
    ticket_id = _ticket_id(client)
    res = client.patch(
        f"/api/tickets/{ticket_id}/triage/runtime",
        json={
            "cli_adapter": "not-valid",
            "claude_model": "",
            "cursor_model": "",
            "lmstudio_base_url": "",
            "lmstudio_model": "",
        },
    )
    assert res.status_code == 400


def test_triage_invoke_uses_runtime_override(client: TestClient, monkeypatch):
    from loregarden.db.session import engine
    from loregarden.models.domain import Ticket, Workspace
    from loregarden.services import triage_service
    from loregarden.services.triage_service import apply_triage_runtime_overrides

    ticket_id = _ticket_id(client)
    client.patch(
        f"/api/tickets/{ticket_id}/triage/runtime",
        json={
            "cli_adapter": "lmstudio",
            "claude_model": "",
            "cursor_model": "",
            "lmstudio_base_url": "http://127.0.0.1:9999/v1",
            "lmstudio_model": "override-model",
        },
    )

    with Session(engine) as session:
        ticket = session.get(Ticket, ticket_id)
        workspace = session.get(Workspace, ticket.workspace_id)
        effective = apply_triage_runtime_overrides(workspace, ticket)
        assert effective.cli_adapter == "lmstudio"
        assert effective.lmstudio_model == "override-model"
        assert effective.lmstudio_base_url == "http://127.0.0.1:9999/v1"

    captured: dict = {}

    def fake_resolve(**kwargs):
        captured.update(kwargs)
        from loregarden.agents.cli_adapters import _local_invocation

        return _local_invocation(
            agent_id=kwargs["agent_id"],
            skill_name=kwargs["skill_name"],
            prompt_file=kwargs["prompt_file"],
        )

    class FakeProc:
        returncode = 0

        def communicate(self, timeout=None):
            return (b"runtime override ok", b"")

        def kill(self):
            return None

    monkeypatch.setattr(triage_service, "resolve_cli_invocation", fake_resolve)
    monkeypatch.setattr(triage_service.subprocess, "Popen", lambda *args, **kwargs: FakeProc())
    monkeypatch.delenv("LOREGARDEN_TRIAGE_STUB_RESPONSE", raising=False)
    monkeypatch.setenv("LOREGARDEN_LMSTUDIO_STUB_RESPONSE", "runtime override ok")

    res = client.post(
        f"/api/tickets/{ticket_id}/triage/messages",
        json={"content": "Which model?"},
    )
    assert res.status_code == 200
    assert captured["workspace"].cli_adapter == "lmstudio"
    assert captured["workspace"].lmstudio_model == "override-model"
    assert "runtime override ok" in res.json()["assistant_message"]["content"]


def test_triage_chat_stub(client: TestClient, monkeypatch):
    monkeypatch.setenv("LOREGARDEN_TRIAGE_STUB_RESPONSE", "Use pytest for backend tests.")
    ticket_id = _ticket_id(client)
    res = client.post(
        f"/api/tickets/{ticket_id}/triage/messages",
        json={"content": "What should I do next?"},
    )
    assert res.status_code == 200
    payload = res.json()
    assert payload["user_message"]["content"] == "What should I do next?"
    assert "pytest" in payload["assistant_message"]["content"].lower()

    snapshot = client.get(f"/api/tickets/{ticket_id}/triage").json()
    assert len(snapshot["messages"]) == 2


def test_triage_messages_persist(client: TestClient, monkeypatch):
    monkeypatch.setenv("LOREGARDEN_TRIAGE_STUB_RESPONSE", "Acknowledged.")
    ticket_id = _ticket_id(client)
    client.post(f"/api/tickets/{ticket_id}/triage/messages", json={"content": "Hello triage"})

    from loregarden.db.session import engine

    with Session(engine) as session:
        ticket = session.get(Ticket, ticket_id)
        messages = session.exec(
            select(TriageMessage).where(TriageMessage.ticket_id == ticket.id)
        ).all()
        assert len(messages) == 2
        assert messages[0].role == "user"
        assert messages[1].role == "assistant"


def test_inbox_filter_by_ticket(client: TestClient):
    ticket_id = _ticket_id(client)
    res = client.get(f"/api/inbox/approvals?ticket_id={ticket_id}")
    assert res.status_code == 200
    assert isinstance(res.json(), list)


def test_triage_includes_workflow_gate_and_cli_approvals(client: TestClient):
    from loregarden.models.domain import Approval, ApprovalKind, ApprovalStatus, Ticket

    ticket_id = _ticket_id(client)

    from loregarden.db.session import engine

    with Session(engine) as session:
        db_ticket = session.get(Ticket, ticket_id)
        assert db_ticket
        session.add(
            Approval(
                ticket_id=ticket_id,
                workspace_id=db_ticket.workspace_id,
                kind=ApprovalKind.WORKFLOW_GATE,
                title="Approve stage completion",
                stage_key="approval",
                impact="Human sign-off required",
                status=ApprovalStatus.PENDING,
            )
        )
        session.add(
            Approval(
                ticket_id=ticket_id,
                workspace_id=db_ticket.workspace_id,
                kind=ApprovalKind.CLI_PERMISSION,
                title="Allow Bash?",
                stage_key=db_ticket.workflow_stage_key,
                impact="Agent requested Bash",
                tool_name="Bash",
                status=ApprovalStatus.PENDING,
            )
        )
        session.commit()

    body = client.get(f"/api/tickets/{ticket_id}/triage").json()
    kinds = {item["kind"] for item in body["pending_approvals"]}
    assert "workflow_gate" in kinds
    assert "cli_permission" in kinds


def test_triage_includes_child_ticket_approvals(client: TestClient):
    from loregarden.models.domain import Approval, ApprovalKind, ApprovalStatus, Ticket, WorkItemType

    from loregarden.db.session import engine

    with Session(engine) as session:
        parent = session.exec(select(Ticket).limit(1)).first()
        assert parent
        child = Ticket(
            external_id="triage-child-task",
            title="Child task for triage rollup",
            workspace_id=parent.workspace_id,
            parent_ticket_id=parent.id,
            work_item_type=WorkItemType.TASK,
        )
        session.add(child)
        session.commit()
        session.refresh(child)
        session.add(
            Approval(
                ticket_id=child.id,
                workspace_id=parent.workspace_id,
                kind=ApprovalKind.WORKFLOW_GATE,
                title="Child stage sign-off",
                stage_key="approval",
                impact="Child needs approval",
                status=ApprovalStatus.PENDING,
            )
        )
        session.commit()
        parent_id = parent.id

    body = client.get(f"/api/tickets/{parent_id}/triage").json()
    titles = [item["title"] for item in body["pending_approvals"]]
    assert "Child stage sign-off" in titles
