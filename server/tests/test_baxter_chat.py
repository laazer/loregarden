"""Home Baxter chat API — one-shot turns against the workspace runtime."""

from fastapi.testclient import TestClient


def test_baxter_chat_message_uses_stub(client: TestClient, monkeypatch):
    monkeypatch.setenv("LOREGARDEN_BAXTER_CHAT_STUB_RESPONSE", "Ship the Home polish next.")
    res = client.post(
        "/api/workspaces/loregarden/baxter-chat/messages",
        json={"content": "What should we ship today?", "history": []},
    )
    assert res.status_code == 200
    assert res.json()["reply"] == "Ship the Home polish next."


def test_baxter_chat_message_rejects_empty(client: TestClient, monkeypatch):
    monkeypatch.delenv("LOREGARDEN_BAXTER_CHAT_STUB_RESPONSE", raising=False)
    res = client.post(
        "/api/workspaces/loregarden/baxter-chat/messages",
        json={"content": "   ", "history": []},
    )
    assert res.status_code == 400
    assert "required" in res.json()["detail"].lower()


def test_baxter_chat_message_unknown_workspace(client: TestClient, monkeypatch):
    monkeypatch.setenv("LOREGARDEN_BAXTER_CHAT_STUB_RESPONSE", "unused")
    res = client.post(
        "/api/workspaces/no-such-ws/baxter-chat/messages",
        json={"content": "Hello", "history": []},
    )
    assert res.status_code == 404


def test_baxter_chat_prompt_includes_snapshot(client: TestClient, monkeypatch):
    from loregarden.services import baxter_chat_service

    captured: dict[str, object] = {}

    def fake_run(profile, *, workspace, prompt, user_prompt=None, **_kwargs):
        captured["profile_stub_env"] = profile.stub_env
        captured["workspace"] = workspace.slug
        captured["prompt"] = prompt
        captured["user_prompt"] = user_prompt
        return "ok from model"

    monkeypatch.delenv("LOREGARDEN_BAXTER_CHAT_STUB_RESPONSE", raising=False)
    monkeypatch.setattr(baxter_chat_service, "run_cli_agent_turn", fake_run)

    res = client.post(
        "/api/workspaces/loregarden/baxter-chat/messages",
        json={
            "content": "What should I look at first?",
            "history": [
                {"role": "user", "content": "Hi"},
                {"role": "assistant", "content": "Hello"},
            ],
        },
    )
    assert res.status_code == 200
    assert res.json()["reply"] == "ok from model"
    assert captured["workspace"] == "loregarden"
    assert captured["profile_stub_env"] == "LOREGARDEN_BAXTER_CHAT_STUB_RESPONSE"
    prompt = str(captured["prompt"])
    assert "What should I look at first?" in prompt
    assert "Live snapshot" in prompt
    assert "Chat UI primitives" in prompt
    assert "user: Hi" in prompt
    assert "assistant: Hello" in prompt
