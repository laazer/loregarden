import json

from fastapi.testclient import TestClient
from loregarden.services.ticket_studio_service import (
    clarifying_questions_resolved,
    format_studio_reply_for_display,
    parse_scope_payload,
)

SCOPE_STUB = """Here is the scoped breakdown:

```json
{
  "summary": "Ticket Studio MVP for feature scoping",
  "clarifying_questions": [],
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

CLARIFY_STUB = """I need a bit more context before scoping.

```json
{
  "summary": "Ticket Studio needs scope decisions on persistence and UX.",
  "clarifying_questions": [
    "Should scope sessions persist after commit?",
    "Is ticket generation always manual?"
  ],
  "tickets": []
}
```
"""


def test_format_studio_reply_for_display_strips_json():
    display = format_studio_reply_for_display(SCOPE_STUB)
    assert "Ticket Studio MVP" in display
    assert "```json" not in display
    assert "3 draft ticket" in display


def test_clarifying_questions_resolved():
    assert clarifying_questions_resolved([], [])
    assert not clarifying_questions_resolved(["Q1"], [])
    assert clarifying_questions_resolved(["Q1"], ["A1"])
    assert not clarifying_questions_resolved(["Q1", "Q2"], ["A1", ""])


def test_parse_scope_payload_extracts_tickets():
    summary, questions, items = parse_scope_payload(SCOPE_STUB)
    assert "Ticket Studio MVP" in summary
    assert len(questions) == 0
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
    assert scoped["clarifying_questions"] == []

    commit = client.post(f"/api/ticket-studio/sessions/{session_id}/commit")
    assert commit.status_code == 200, commit.text
    result = commit.json()
    assert result["created_count"] == 3
    assert len(result["created_ticket_ids"]) == 3

    session_after = client.get(f"/api/ticket-studio/sessions/{session_id}").json()
    assert session_after["status"] == "committed"

    tickets = client.get("/api/tickets?workspace=loregarden&search=Ticket+Studio").json()
    assert any(t["title"] == "Ticket Studio" and t["work_item_type"] == "feature" for t in tickets)
    assert any(
        t["title"] == "Add ticket studio routes" and t["work_item_type"] == "task" for t in tickets
    )

    dup_commit = client.post(f"/api/ticket-studio/sessions/{session_id}/commit")
    assert dup_commit.status_code == 400


def test_ticket_studio_scope_surfaces_root_milestone_in_draft(client: TestClient, monkeypatch):
    monkeypatch.setenv("LOREGARDEN_TICKET_STUDIO_STUB_RESPONSE", SCOPE_STUB)

    create = client.post(
        "/api/ticket-studio/sessions",
        json={
            "workspace_slug": "loregarden",
            "title": "Ticket Studio feature",
            "brief": "Scope the ticket studio MVP.",
        },
    )
    assert create.status_code == 200
    session_id = create.json()["id"]

    scope = client.post(f"/api/ticket-studio/sessions/{session_id}/scope")
    assert scope.status_code == 200, scope.text
    draft = scope.json()["draft"]
    # 3 model-proposed tickets + 1 milestone synthesized to give the root feature a legal parent
    assert len(draft) == 4
    milestone_item = next(item for item in draft if item["work_item_type"] == "milestone")
    assert milestone_item["title"] == "Ticket Studio feature"
    feature_item = next(item for item in draft if item["title"] == "Ticket Studio")
    assert feature_item["parent_ref"] == milestone_item["ref"]

    commit = client.post(f"/api/ticket-studio/sessions/{session_id}/commit")
    assert commit.status_code == 200, commit.text
    result = commit.json()
    # 3 draft tickets + 1 synthesized milestone parent for the root feature
    assert result["created_count"] == 4
    assert len(result["created_ticket_ids"]) == 4

    tickets = {
        t["id"]: t
        for t in client.get("/api/tickets?workspace=loregarden&search=Ticket+Studio").json()
    }
    feature = next(
        t
        for t in tickets.values()
        if t["title"] == "Ticket Studio" and t["work_item_type"] == "feature"
    )
    milestone = tickets[feature["parent_ticket_id"]]
    assert milestone["work_item_type"] == "milestone"
    assert milestone["title"] == "Ticket Studio feature"


def test_ticket_studio_scope_survives_large_json_payload(client: TestClient, monkeypatch):
    from loregarden.agents.cli_adapters import _local_invocation
    from loregarden.services import ticket_studio_service

    tickets = [
        {
            "ref": f"task-{i}",
            "work_item_type": "task",
            "parent_ref": None,
            "title": f"Task {i}: migrate subsystem component {i}",
            "description": "Detailed migration steps and constraints. " * 20,
            "acceptance_criteria": [f"Criterion {j} for task {i}" for j in range(5)],
            "priority": 2,
            "suggested_agent": "backend_implementer",
        }
        for i in range(60)
    ]
    payload = {
        "summary": "Large decomposition covering many independent migration tasks",
        "clarifying_questions": [],
        "tickets": tickets,
    }
    large_reply = "Here is the scoped breakdown:\n\n```json\n" + json.dumps(payload) + "\n```\n"
    # Sanity check the fixture actually exceeds the old blanket truncation cap.
    assert len(large_reply) > 12000

    class FakeProc:
        returncode = 0

        def communicate(self, timeout=None):
            return (large_reply.encode("utf-8"), b"")

        def kill(self):
            return None

    def fake_resolve(**kwargs):
        return _local_invocation(
            agent_id=kwargs["agent_id"],
            skill_name=kwargs["skill_name"],
            prompt_file=kwargs["prompt_file"],
        )

    monkeypatch.setattr(ticket_studio_service, "build_triage_invocation", fake_resolve)
    monkeypatch.setattr(
        ticket_studio_service.subprocess, "Popen", lambda *args, **kwargs: FakeProc()
    )
    monkeypatch.delenv("LOREGARDEN_TICKET_STUDIO_STUB_RESPONSE", raising=False)

    create = client.post(
        "/api/ticket-studio/sessions",
        json={
            "workspace_slug": "loregarden",
            "title": "Large scope",
            "brief": "Decompose a big migration into many independent tasks.",
        },
    )
    assert create.status_code == 200
    session_id = create.json()["id"]

    scope = client.post(f"/api/ticket-studio/sessions/{session_id}/scope")
    assert scope.status_code == 200, scope.text
    draft = scope.json()["draft"]
    # 60 model-proposed tasks + 1 milestone synthesized as their legal root parent.
    assert len(draft) == 61
    assert sum(1 for item in draft if item["work_item_type"] == "task") == 60


def test_ticket_studio_clarify_then_scope(client: TestClient, monkeypatch):
    monkeypatch.setenv("LOREGARDEN_TICKET_STUDIO_STUB_RESPONSE", CLARIFY_STUB)

    create = client.post(
        "/api/ticket-studio/sessions",
        json={
            "workspace_slug": "loregarden",
            "title": "Clarify flow",
            "brief": "Ambiguous feature brief.",
        },
    )
    session_id = create.json()["id"]

    clarify = client.post(f"/api/ticket-studio/sessions/{session_id}/clarify")
    assert clarify.status_code == 200, clarify.text
    body = clarify.json()
    assert len(body["clarifying_questions"]) == 2
    assert body["clarifying_resolved"] is False
    assert body["draft"] == []

    blocked = client.post(f"/api/ticket-studio/sessions/{session_id}/scope")
    assert blocked.status_code == 400

    saved = client.patch(
        f"/api/ticket-studio/sessions/{session_id}/clarifications",
        json={"answers": ["Yes, keep sessions.", "Manual commit only."]},
    )
    assert saved.status_code == 200
    assert saved.json()["clarifying_resolved"] is True

    monkeypatch.setenv("LOREGARDEN_TICKET_STUDIO_STUB_RESPONSE", SCOPE_STUB)
    scope = client.post(f"/api/ticket-studio/sessions/{session_id}/scope")
    assert scope.status_code == 200, scope.text
    # 3 model-proposed tickets + 1 milestone synthesized for the parentless root feature
    assert len(scope.json()["draft"]) == 4


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
    assert body["messages"][-1]["display_content"]
    assert "```json" not in body["messages"][-1]["display_content"]
    assert len(body["draft"]) == 0


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
