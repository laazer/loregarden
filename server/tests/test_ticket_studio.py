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


SECOND_ROUND_STUB = """One more thing before I scope.

```json
{
  "summary": "Persistence settled; rollout still open.",
  "clarifying_questions": [
    "Which environments get this first?",
    "Who approves the rollout?"
  ],
  "tickets": []
}
```
"""

READY_STUB = """That covers it.

```json
{
  "summary": "I have everything I need to generate tickets.",
  "clarifying_questions": [],
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


def test_parse_scope_payload_tolerates_a_dropped_suggested_agent():
    """`suggested_agent` was removed because nothing routed on it, but sessions
    saved before that still carry the key and models still volunteer it."""
    payload = json.dumps(
        {
            "summary": "s",
            "clarifying_questions": [],
            "tickets": [
                {
                    "ref": "t1",
                    "work_item_type": "task",
                    "title": "Build it",
                    "suggested_agent": "backend_implementer",
                }
            ],
        }
    )
    _, _, items = parse_scope_payload(f"```json\n{payload}\n```")
    assert len(items) == 1
    assert not hasattr(items[0], "suggested_agent")


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
    assert scope.status_code == 202, scope.text
    scoped = scope.json()
    assert len(scoped["draft"]) == 3
    assert scoped["summary"]
    assert scoped["clarifying_questions"] == []

    commit = client.post(f"/api/ticket-studio/sessions/{session_id}/commit")
    assert commit.status_code == 200, commit.text
    result = commit.json()
    # feature + capability + task, plus one auto-added integration-review capability
    # under the feature (which now has a child).
    assert result["created_count"] == 4
    assert len(result["created_ticket_ids"]) == 4
    assert result["root_ticket_id"] == milestone_id
    assert sum(result["breakdown"].values()) == result["created_count"]

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
    assert scope.status_code == 202, scope.text
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
    # 3 draft tickets + 1 synthesized milestone parent, plus 2 auto-added
    # integration-review children (one under the milestone, one under the feature —
    # each now has a child).
    assert result["created_count"] == 6
    assert len(result["created_ticket_ids"]) == 6

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
    assert result["root_ticket_id"] == milestone["id"]
    assert result["breakdown"].get("milestone") == 1


def test_ticket_studio_scope_survives_large_json_payload(client: TestClient, monkeypatch):
    from loregarden.agents.cli_adapters import _local_invocation
    from loregarden.services import cli_agent_runner

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

    class FakeStdout:
        """The pipe a scoper turn is now read from line by line."""

        def __init__(self, text: str) -> None:
            self._lines = text.splitlines(keepends=True)

        def readline(self) -> str:
            return self._lines.pop(0) if self._lines else ""

    class FakeProc:
        returncode = 0

        def __init__(self) -> None:
            self.stdout = FakeStdout(large_reply)
            self.stderr = None

        def poll(self):
            return 0 if not self.stdout._lines else None

        def communicate(self, timeout=None):
            rest = "".join(self.stdout._lines)
            self.stdout._lines = []
            return (rest.encode("utf-8"), b"")

        def kill(self):
            return None

    def fake_resolve(**kwargs):
        return _local_invocation(
            agent_id=kwargs["agent_id"],
            skill_name=kwargs["skill_name"],
            prompt_file=kwargs["prompt_file"],
        )

    monkeypatch.setattr(cli_agent_runner, "build_triage_invocation", fake_resolve)
    monkeypatch.setattr(cli_agent_runner.subprocess, "Popen", lambda *args, **kwargs: FakeProc())
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
    assert scope.status_code == 202, scope.text
    draft = scope.json()["draft"]
    # Every model-proposed task survives the large payload...
    assert sum(1 for item in draft if item["work_item_type"] == "task") == 60
    # ...under one synthesized milestone → feature → capability spine, since a task may
    # not hang off a milestone directly.
    assert len(draft) == 63
    assert [item["work_item_type"] for item in draft[:3]] == ["milestone", "feature", "capability"]
    commit = client.post(f"/api/ticket-studio/sessions/{session_id}/commit")
    assert commit.status_code == 200, commit.text


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
    assert clarify.status_code == 202, clarify.text
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
    assert saved.status_code == 202
    assert saved.json()["clarifying_resolved"] is True

    monkeypatch.setenv("LOREGARDEN_TICKET_STUDIO_STUB_RESPONSE", SCOPE_STUB)
    scope = client.post(f"/api/ticket-studio/sessions/{session_id}/scope")
    assert scope.status_code == 202, scope.text
    # 3 model-proposed tickets + 1 milestone synthesized for the parentless root feature
    assert len(scope.json()["draft"]) == 4


def _clarified_session(client: TestClient, title: str) -> str:
    create = client.post(
        "/api/ticket-studio/sessions",
        json={"workspace_slug": "loregarden", "title": title, "brief": "Ambiguous brief."},
    )
    session_id = create.json()["id"]
    client.post(f"/api/ticket-studio/sessions/{session_id}/clarify")
    return session_id


def test_saving_answers_hands_straight_back_to_the_scoper(client: TestClient, monkeypatch):
    """The operator answers; the scoper picks it up without another button press."""
    monkeypatch.setenv("LOREGARDEN_TICKET_STUDIO_STUB_RESPONSE", CLARIFY_STUB)
    session_id = _clarified_session(client, "Auto-continue")

    monkeypatch.setenv("LOREGARDEN_TICKET_STUDIO_STUB_RESPONSE", READY_STUB)
    saved = client.patch(
        f"/api/ticket-studio/sessions/{session_id}/clarifications",
        json={"answers": ["Yes, keep them.", "Manual only."]},
    )
    assert saved.status_code == 202, saved.text
    body = saved.json()

    # The scoper had nothing left to ask, so the round is closed out rather than left
    # on screen, and it says so.
    assert body["clarifying_questions"] == []
    assert body["clarifying_resolved"] is True
    assert "everything I need" in body["summary"]

    # The answers and the reply are both in the conversation, newest last.
    roles = [message["role"] for message in body["messages"]]
    assert roles[-2:] == ["user", "assistant"]
    answers_message = body["messages"][-2]["content"]
    assert "Yes, keep them." in answers_message
    assert "Should scope sessions persist after commit?" in answers_message

    # Generation is unblocked without a further round trip.
    monkeypatch.setenv("LOREGARDEN_TICKET_STUDIO_STUB_RESPONSE", SCOPE_STUB)
    assert client.post(f"/api/ticket-studio/sessions/{session_id}/scope").status_code == 202


def test_a_follow_up_round_of_questions_starts_blank(client: TestClient, monkeypatch):
    """Answers used to carry over by position onto entirely different questions."""
    monkeypatch.setenv("LOREGARDEN_TICKET_STUDIO_STUB_RESPONSE", CLARIFY_STUB)
    session_id = _clarified_session(client, "Second round")

    monkeypatch.setenv("LOREGARDEN_TICKET_STUDIO_STUB_RESPONSE", SECOND_ROUND_STUB)
    saved = client.patch(
        f"/api/ticket-studio/sessions/{session_id}/clarifications",
        json={"answers": ["Yes, keep them.", "Manual only."]},
    )
    assert saved.status_code == 202, saved.text
    body = saved.json()

    assert body["clarifying_questions"] == [
        "Which environments get this first?",
        "Who approves the rollout?",
    ]
    assert body["clarifying_answers"] == ["", ""]
    assert body["clarifying_resolved"] is False

    # The first round's answers survive in the conversation rather than being dropped.
    assert any("Manual only." in message["content"] for message in body["messages"])


def test_a_repeated_question_keeps_its_answer(client: TestClient, monkeypatch):
    monkeypatch.setenv("LOREGARDEN_TICKET_STUDIO_STUB_RESPONSE", CLARIFY_STUB)
    session_id = _clarified_session(client, "Same round again")

    # The stub replies with the very same questions, so the answers still apply.
    saved = client.patch(
        f"/api/ticket-studio/sessions/{session_id}/clarifications",
        json={"answers": ["Yes, keep them.", "Manual only."]},
    )
    assert saved.status_code == 202, saved.text
    assert saved.json()["clarifying_answers"] == ["Yes, keep them.", "Manual only."]
    assert saved.json()["clarifying_resolved"] is True


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
    assert msg.status_code == 202, msg.text
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


def _draft_session(client: TestClient, title: str) -> str:
    create = client.post(
        "/api/ticket-studio/sessions",
        json={"workspace_slug": "loregarden", "title": title, "brief": "Feature brief"},
    )
    assert create.status_code == 200
    return create.json()["id"]


def test_scope_turn_does_not_run_on_the_request_thread(client: TestClient, monkeypatch):
    """The POST records the turn and hands off — it does not call the CLI inline.

    Patching the scheduler proves it: the old blocking endpoint held the request
    open for the length of a scope call, so a reload or a restart lost the work.
    """
    from unittest.mock import patch

    monkeypatch.setenv("LOREGARDEN_TICKET_STUDIO_STUB_RESPONSE", SCOPE_STUB)
    session_id = _draft_session(client, "Async scope")

    with patch("loregarden.api.ticket_studio.schedule_studio_turn") as scheduled:
        res = client.post(f"/api/ticket-studio/sessions/{session_id}/scope")

    assert res.status_code == 202
    assert scheduled.call_count == 1
    body = res.json()
    assert body["run_status"] == "running"
    assert body["active_turn_id"]
    # Nothing was applied: the turn has not run.
    assert body["draft"] == []


def test_pending_turn_is_not_shown_as_a_message(client: TestClient, monkeypatch):
    from unittest.mock import patch

    monkeypatch.setenv("LOREGARDEN_TICKET_STUDIO_STUB_RESPONSE", SCOPE_STUB)
    session_id = _draft_session(client, "Pending turn")

    with patch("loregarden.api.ticket_studio.schedule_studio_turn"):
        client.post(f"/api/ticket-studio/sessions/{session_id}/scope")

    detail = client.get(f"/api/ticket-studio/sessions/{session_id}").json()
    # The operator's turn is there; the empty assistant row in flight is not.
    assert [m["role"] for m in detail["messages"]] == ["user"]
    assert detail["run_status"] == "running"


def test_second_turn_rejected_while_one_is_in_flight(client: TestClient, monkeypatch):
    from unittest.mock import patch

    monkeypatch.setenv("LOREGARDEN_TICKET_STUDIO_STUB_RESPONSE", SCOPE_STUB)
    session_id = _draft_session(client, "Busy session")

    with patch("loregarden.api.ticket_studio.schedule_studio_turn"):
        assert client.post(f"/api/ticket-studio/sessions/{session_id}/scope").status_code == 202
        clash = client.post(
            f"/api/ticket-studio/sessions/{session_id}/messages",
            json={"content": "and another thing"},
        )
    assert clash.status_code == 409
    assert "still working" in clash.json()["detail"]


def test_interrupted_studio_turn_is_settled_not_stranded(client: TestClient, monkeypatch):
    """A turn orphaned by a restart must fail loudly rather than block the session."""
    from unittest.mock import patch

    from loregarden.db.session import engine
    from loregarden.services.ticket_studio_run_service import fail_interrupted_studio_turns
    from sqlmodel import Session

    monkeypatch.setenv("LOREGARDEN_TICKET_STUDIO_STUB_RESPONSE", SCOPE_STUB)
    session_id = _draft_session(client, "Interrupted")

    with patch("loregarden.api.ticket_studio.schedule_studio_turn"):
        client.post(f"/api/ticket-studio/sessions/{session_id}/scope")

    with Session(engine) as db:
        assert len(fail_interrupted_studio_turns(db)) == 1

    detail = client.get(f"/api/ticket-studio/sessions/{session_id}").json()
    assert detail["run_status"] == "idle"
    assert "interrupted" in detail["messages"][-1]["content"].lower()
    # And the session takes work again rather than staying wedged.
    assert client.post(f"/api/ticket-studio/sessions/{session_id}/scope").status_code == 202


def test_failed_turn_is_recorded_as_failed(client: TestClient, monkeypatch):
    from loregarden.services import ticket_studio_service

    def boom(*_args, **_kwargs):
        raise RuntimeError("model exploded")

    monkeypatch.delenv("LOREGARDEN_TICKET_STUDIO_STUB_RESPONSE", raising=False)
    session_id = _draft_session(client, "Failing turn")
    monkeypatch.setattr(ticket_studio_service, "run_cli_agent_turn", boom)

    res = client.post(f"/api/ticket-studio/sessions/{session_id}/scope")
    assert res.status_code == 202
    body = res.json()
    # Settled, not stuck — and the failure reads in the thread.
    assert body["run_status"] == "idle"
    assert "unavailable" in body["messages"][-1]["content"]
    assert "model exploded" in body["messages"][-1]["content"]


def test_bootstrap_clarify_chains_scope_when_nothing_is_unclear(client: TestClient, monkeypatch):
    """New-session bootstrap: no questions means generate, without a second call.

    The chain used to live in the client, which awaited two blocking requests —
    a reload between them left the session on an empty draft forever.
    """
    monkeypatch.setenv("LOREGARDEN_TICKET_STUDIO_STUB_RESPONSE", SCOPE_STUB)
    session_id = _draft_session(client, "Bootstrap")

    res = client.post(
        f"/api/ticket-studio/sessions/{session_id}/clarify", json={"auto_scope": True}
    )
    assert res.status_code == 202
    body = res.json()
    assert body["clarifying_questions"] == []
    # The scope turn ran off the back of the clarify turn: the draft is populated
    # (under the synthesized root the repair adds for a parentless session).
    titles = [item["title"] for item in body["draft"]]
    assert "Ticket Studio" in titles
    assert "Add ticket studio routes" in titles
    assert body["run_status"] == "idle"


def test_bootstrap_clarify_stops_when_the_scoper_has_questions(client: TestClient, monkeypatch):
    monkeypatch.setenv("LOREGARDEN_TICKET_STUDIO_STUB_RESPONSE", CLARIFY_STUB)
    session_id = _draft_session(client, "Bootstrap with questions")

    body = client.post(
        f"/api/ticket-studio/sessions/{session_id}/clarify", json={"auto_scope": True}
    ).json()
    assert body["clarifying_questions"]
    # Questions are open, so nothing was generated over the top of them.
    assert body["draft"] == []
