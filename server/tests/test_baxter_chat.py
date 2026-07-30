"""Home Baxter chat API — persisted sessions, turns, and archive listing."""

from fastapi.testclient import TestClient


def _new_session(client: TestClient) -> str:
    res = client.post("/api/workspaces/loregarden/baxter-chat/sessions", json={})
    assert res.status_code == 201
    return res.json()["id"]


def test_baxter_chat_turn_persists_both_messages(client: TestClient, monkeypatch):
    monkeypatch.setenv("LOREGARDEN_BAXTER_CHAT_STUB_RESPONSE", "Ship the Home polish next.")
    session_id = _new_session(client)

    res = client.post(
        f"/api/workspaces/loregarden/baxter-chat/sessions/{session_id}/messages",
        json={"content": "What should we ship today?"},
    )
    assert res.status_code == 202

    snapshot = client.get(f"/api/workspaces/loregarden/baxter-chat/sessions/{session_id}").json()
    assert [m["role"] for m in snapshot["messages"]] == ["user", "assistant"]
    assert snapshot["messages"][1]["content"] == "Ship the Home polish next."
    assert snapshot["run_status"] == "idle"


def test_baxter_chat_history_survives_a_fresh_read(client: TestClient, monkeypatch):
    monkeypatch.setenv("LOREGARDEN_BAXTER_CHAT_STUB_RESPONSE", "First answer.")
    session_id = _new_session(client)
    client.post(
        f"/api/workspaces/loregarden/baxter-chat/sessions/{session_id}/messages",
        json={"content": "First question"},
    )

    # A reload is just another GET — the thread must come back from the database.
    snapshot = client.get(f"/api/workspaces/loregarden/baxter-chat/sessions/{session_id}").json()
    assert len(snapshot["messages"]) == 2
    assert snapshot["messages"][0]["content"] == "First question"


def test_baxter_chat_session_titled_from_first_message(client: TestClient, monkeypatch):
    monkeypatch.setenv("LOREGARDEN_BAXTER_CHAT_STUB_RESPONSE", "ok")
    session_id = _new_session(client)
    client.post(
        f"/api/workspaces/loregarden/baxter-chat/sessions/{session_id}/messages",
        json={"content": "Triage the stuck tickets"},
    )

    sessions = client.get("/api/workspaces/loregarden/baxter-chat/sessions").json()
    entry = next(item for item in sessions if item["id"] == session_id)
    assert entry["title"] == "Triage the stuck tickets"
    assert entry["message_count"] == 2
    assert entry["preview"] == "ok"


def test_baxter_chat_rename_and_delete(client: TestClient, monkeypatch):
    monkeypatch.setenv("LOREGARDEN_BAXTER_CHAT_STUB_RESPONSE", "ok")
    session_id = _new_session(client)
    client.post(
        f"/api/workspaces/loregarden/baxter-chat/sessions/{session_id}/messages",
        json={"content": "Anything urgent?"},
    )

    renamed = client.patch(
        f"/api/workspaces/loregarden/baxter-chat/sessions/{session_id}",
        json={"title": "Morning triage"},
    )
    assert renamed.status_code == 200
    assert renamed.json()["title"] == "Morning triage"

    assert (
        client.delete(f"/api/workspaces/loregarden/baxter-chat/sessions/{session_id}").status_code
        == 200
    )
    assert (
        client.get(f"/api/workspaces/loregarden/baxter-chat/sessions/{session_id}").status_code
        == 404
    )
    assert client.get("/api/workspaces/loregarden/baxter-chat/sessions").json() == []


def test_baxter_chat_message_rejects_empty(client: TestClient, monkeypatch):
    monkeypatch.delenv("LOREGARDEN_BAXTER_CHAT_STUB_RESPONSE", raising=False)
    session_id = _new_session(client)
    res = client.post(
        f"/api/workspaces/loregarden/baxter-chat/sessions/{session_id}/messages",
        json={"content": "   "},
    )
    assert res.status_code == 400
    assert "required" in res.json()["detail"].lower()


def test_baxter_chat_unknown_workspace(client: TestClient):
    res = client.get("/api/workspaces/no-such-ws/baxter-chat/sessions")
    assert res.status_code == 404


def test_baxter_chat_session_scoped_to_its_workspace(client: TestClient, monkeypatch):
    """An id from another workspace is not readable through this one."""
    monkeypatch.setenv("LOREGARDEN_BAXTER_CHAT_STUB_RESPONSE", "ok")
    other = client.post(
        "/api/workspaces",
        json={"slug": "other-ws", "name": "Other", "repo_path": "/tmp/other-ws"},
    )
    assert other.status_code in (200, 201)
    session_id = _new_session(client)

    res = client.get(f"/api/workspaces/other-ws/baxter-chat/sessions/{session_id}")
    assert res.status_code == 404


def test_baxter_chat_prompt_includes_snapshot_and_stored_history(client: TestClient, monkeypatch):
    from loregarden.services import baxter_chat_service

    captured: dict[str, object] = {}

    def fake_run(profile, *, workspace, prompt, user_prompt=None, **_kwargs):
        captured["profile_stub_env"] = profile.stub_env
        captured["workspace"] = workspace.slug
        captured["prompt"] = prompt
        return "ok from model"

    session_id = _new_session(client)
    monkeypatch.setenv("LOREGARDEN_BAXTER_CHAT_STUB_RESPONSE", "Hello")
    client.post(
        f"/api/workspaces/loregarden/baxter-chat/sessions/{session_id}/messages",
        json={"content": "Hi"},
    )

    monkeypatch.delenv("LOREGARDEN_BAXTER_CHAT_STUB_RESPONSE", raising=False)
    monkeypatch.setattr(baxter_chat_service, "run_cli_agent_turn", fake_run)
    client.post(
        f"/api/workspaces/loregarden/baxter-chat/sessions/{session_id}/messages",
        json={"content": "What should I look at first?"},
    )

    assert captured["workspace"] == "loregarden"
    assert captured["profile_stub_env"] == "LOREGARDEN_BAXTER_CHAT_STUB_RESPONSE"
    prompt = str(captured["prompt"])
    assert "What should I look at first?" in prompt
    assert "Live snapshot" in prompt
    assert "Chat UI primitives" in prompt
    # History comes from the stored thread, not from the client.
    assert "user: Hi" in prompt
    assert "assistant: Hello" in prompt


def test_interrupted_turn_is_settled_not_stranded(client: TestClient, monkeypatch):
    """A turn orphaned by a restart must fail loudly rather than hold the composer."""
    from loregarden.db.session import engine
    from loregarden.models.domain import BaxterChatMessage
    from loregarden.services.baxter_chat_run_service import fail_interrupted_baxter_chat_turns
    from sqlmodel import Session

    session_id = _new_session(client)
    with Session(engine) as db:
        db.add(BaxterChatMessage(session_id=session_id, role="assistant", status="pending"))
        db.commit()

    snapshot = client.get(f"/api/workspaces/loregarden/baxter-chat/sessions/{session_id}").json()
    assert snapshot["run_status"] == "running"

    with Session(engine) as db:
        assert len(fail_interrupted_baxter_chat_turns(db)) == 1

    snapshot = client.get(f"/api/workspaces/loregarden/baxter-chat/sessions/{session_id}").json()
    assert snapshot["run_status"] == "idle"
    assert "interrupted" in snapshot["messages"][-1]["content"].lower()


def test_second_turn_rejected_while_one_is_in_flight(client: TestClient):
    from loregarden.db.session import engine
    from loregarden.models.domain import BaxterChatMessage
    from sqlmodel import Session

    session_id = _new_session(client)
    with Session(engine) as db:
        db.add(BaxterChatMessage(session_id=session_id, role="assistant", status="pending"))
        db.commit()

    res = client.post(
        f"/api/workspaces/loregarden/baxter-chat/sessions/{session_id}/messages",
        json={"content": "Another question"},
    )
    assert res.status_code == 409
