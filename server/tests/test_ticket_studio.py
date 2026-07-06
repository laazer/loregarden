from fastapi.testclient import TestClient

from loregarden.services.ticket_studio_service import parse_scope_payload


SCOPE_STUB = """Here is the scoped breakdown:

```json
{
  "summary": "Ticket Studio MVP for feature scoping",
  "clarifying_questions": ["Should scope sessions persist after commit?"],
  "tickets": [
    {
      "ref": "feature-1",
      "work_item_type": "feature",
      "parent_ref": null,
      "title": "Ticket Studio",
      "description": "Agent-assisted feature scoping UI",
      "acceptance_criteria": ["Operator can generate draft tickets"],
      "priority": 2,
      "suggested_agent": "planner"
    },
    {
      "ref": "cap-1",
      "work_item_type": "capability",
      "parent_ref": "feature-1",
      "title": "Ticket Studio API",
      "description": "Backend session and commit endpoints",
      "acceptance_criteria": ["Sessions CRUD works", "Commit creates hierarchy"],
      "priority": 2,
      "suggested_agent": "backend_implementer"
    },
    {
      "ref": "task-1",
      "work_item_type": "task",
      "parent_ref": "cap-1",
      "title": "Add ticket studio routes",
      "description": "REST API for sessions",
      "acceptance_criteria": ["Tests pass"],
      "priority": 2,
      "suggested_agent": "backend_implementer"
    }
  ]
}
```
"""


def test_parse_scope_payload_extracts_tickets():
    summary, questions, items = parse_scope_payload(SCOPE_STUB)
    assert "Ticket Studio MVP" in summary
    assert len(questions) == 1
    assert len(items) == 3
    assert items[0].work_item_type.value == "feature"
    assert items[1].parent_ref == "feature-1"


def test_ticket_studio_session_crud(client: TestClient):
    create = client.post(
        "/api/ticket-studio/sessions",
        json={
            "workspace_slug": "loregarden",
            "title": "Scope test feature",
            "brief": "Build a widget for operators.",
        },
    )
    assert create.status_code == 200, create.text
    body = create.json()
    session_id = body["id"]
    assert body["title"] == "Scope test feature"
    assert body["status"] == "draft"
    assert body["draft"] == []

    listed = client.get("/api/ticket-studio/sessions?workspace=loregarden")
    assert listed.status_code == 200
    assert any(item["id"] == session_id for item in listed.json())

    detail = client.get(f"/api/ticket-studio/sessions/{session_id}")
    assert detail.status_code == 200
    assert detail.json()["brief"].startswith("Build a widget")

    delete = client.delete(f"/api/ticket-studio/sessions/{session_id}")
    assert delete.status_code == 200
    assert client.get(f"/api/ticket-studio/sessions/{session_id}").status_code == 404


def test_ticket_studio_scope_and_commit(client: TestClient, monkeypatch):
    monkeypatch.setenv("LOREGARDEN_TICKET_STUDIO_STUB_RESPONSE", SCOPE_STUB)

    milestone_id = next(
        t["id"]
        for t in client.get("/api/tickets?workspace=loregarden").json()
        if t["work_item_type"] == "milestone"
    )

    create = client.post(
        "/api/ticket-studio/sessions",
        json={
            "workspace_slug": "loregarden",
            "title": "Ticket Studio feature",
            "brief": "Scope the ticket studio MVP.",
            "parent_ticket_id": milestone_id,
        },
    )
    assert create.status_code == 200
    session_id = create.json()["id"]

    scope = client.post(f"/api/ticket-studio/sessions/{session_id}/scope")
    assert scope.status_code == 200, scope.text
    scoped = scope.json()
    assert len(scoped["draft"]) == 3
    assert scoped["summary"]
    assert scoped["clarifying_questions"]

    commit = client.post(f"/api/ticket-studio/sessions/{session_id}/commit")
    assert commit.status_code == 200, commit.text
    result = commit.json()
    assert result["created_count"] == 3
    assert len(result["created_ticket_ids"]) == 3

    session_after = client.get(f"/api/ticket-studio/sessions/{session_id}").json()
    assert session_after["status"] == "committed"

    tickets = client.get("/api/tickets?workspace=loregarden&search=Ticket+Studio").json()
    assert any(t["title"] == "Ticket Studio" and t["work_item_type"] == "feature" for t in tickets)
    assert any(t["title"] == "Add ticket studio routes" and t["work_item_type"] == "task" for t in tickets)

    dup_commit = client.post(f"/api/ticket-studio/sessions/{session_id}/commit")
    assert dup_commit.status_code == 400


def test_ticket_studio_chat_applies_scope_from_stub(client: TestClient, monkeypatch):
    monkeypatch.setenv("LOREGARDEN_TICKET_STUDIO_STUB_RESPONSE", SCOPE_STUB)

    create = client.post(
        "/api/ticket-studio/sessions",
        json={
            "workspace_slug": "loregarden",
            "title": "Chat scope test",
            "brief": "Feature brief",
        },
    )
    session_id = create.json()["id"]

    msg = client.post(
        f"/api/ticket-studio/sessions/{session_id}/messages",
        json={"content": "Please scope this into tickets"},
    )
    assert msg.status_code == 200, msg.text
    body = msg.json()
    assert len(body["messages"]) == 2
    assert len(body["draft"]) == 3


def test_ticket_studio_draft_validation(client: TestClient, monkeypatch):
    monkeypatch.setenv("LOREGARDEN_TICKET_STUDIO_STUB_RESPONSE", SCOPE_STUB)

    create = client.post(
        "/api/ticket-studio/sessions",
        json={
            "workspace_slug": "loregarden",
            "title": "Invalid draft",
            "brief": "Test",
        },
    )
    session_id = create.json()["id"]
    client.post(f"/api/ticket-studio/sessions/{session_id}/scope")

    bad = client.patch(
        f"/api/ticket-studio/sessions/{session_id}/draft",
        json={
            "items": [
                {
                    "ref": "task-1",
                    "work_item_type": "task",
                    "parent_ref": None,
                    "title": "Orphan task",
                    "description": "",
                    "acceptance_criteria": [],
                    "priority": 3,
                    "suggested_agent": "",
                    "selected": True,
                }
            ]
        },
    )
    assert bad.status_code == 400


def test_ticket_studio_runtime_persists(client: TestClient):
    create = client.post(
        "/api/ticket-studio/sessions",
        json={
            "workspace_slug": "loregarden",
            "title": "Runtime test",
            "brief": "",
        },
    )
    session_id = create.json()["id"]

    res = client.patch(
        f"/api/ticket-studio/sessions/{session_id}/runtime",
        json={
            "cli_adapter": "lmstudio",
            "claude_model": "",
            "cursor_model": "",
            "lmstudio_base_url": "http://127.0.0.1:1234/v1",
            "lmstudio_model": "studio-model",
        },
    )
    assert res.status_code == 200
    assert res.json()["lmstudio_model"] == "studio-model"

    detail = client.get(f"/api/ticket-studio/sessions/{session_id}").json()
    assert detail["runtime"]["cli_adapter"] == "lmstudio"
