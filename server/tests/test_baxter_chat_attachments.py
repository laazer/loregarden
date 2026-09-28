"""Home chat attachments — upload, send, prompt, fork and delete."""

from unittest.mock import patch

from fastapi.testclient import TestClient
from loregarden.services.agent_turn_runner import AgentTurnRequest, AgentTurnResult
from loregarden.services.chat_attachments import session_attachments_dir

BASE = "/api/workspaces/loregarden/baxter-chat/sessions"
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32


def _new_session(client: TestClient) -> str:
    res = client.post(BASE, json={})
    assert res.status_code == 201
    return res.json()["id"]


def _upload(client: TestClient, session_id: str, name: str, data: bytes, mime: str):
    return client.post(f"{BASE}/{session_id}/attachments", files={"file": (name, data, mime)})


class _CapturingTurn:
    def __init__(self) -> None:
        self.requests: list[AgentTurnRequest] = []

    def __call__(self, request: AgentTurnRequest) -> AgentTurnResult:
        self.requests.append(request)
        return AgentTurnResult(reply="read it", strategy="read_only", adapter="claude")


def test_text_attachment_is_inlined_into_the_prompt_and_shown_on_the_turn(
    client: TestClient, monkeypatch
):
    monkeypatch.delenv("LOREGARDEN_BAXTER_CHAT_STUB_RESPONSE", raising=False)
    session_id = _new_session(client)
    uploaded = _upload(client, session_id, "trace.log", b"boom at line 42\n", "text/plain")
    assert uploaded.status_code == 201
    attachment = uploaded.json()
    assert attachment["kind"] == "text"

    turn = _CapturingTurn()
    with patch("loregarden.services.baxter_chat_service.run_agent_turn", turn):
        res = client.post(
            f"{BASE}/{session_id}/messages",
            json={"content": "Why did this fail?", "attachment_ids": [attachment["id"]]},
        )
    assert res.status_code == 202

    snapshot = client.get(f"{BASE}/{session_id}").json()
    user = snapshot["messages"][0]
    assert user["content"] == "Why did this fail?"
    assert [a["name"] for a in user["attachments"]] == ["trace.log"]
    assert snapshot["messages"][1]["content"] == "read it"

    [request] = turn.requests
    assert "boom at line 42" in request.prompt
    # Text needs no directory grant; it is already in the prompt.
    assert request.extra_dirs == ()


def test_image_attachment_grants_its_directory_on_claude(client: TestClient, monkeypatch):
    monkeypatch.delenv("LOREGARDEN_BAXTER_CHAT_STUB_RESPONSE", raising=False)
    session_id = _new_session(client)
    attachment = _upload(client, session_id, "shot.png", PNG, "image/png").json()
    assert attachment["kind"] == "image"

    turn = _CapturingTurn()
    with (
        patch("loregarden.services.baxter_chat_service.run_agent_turn", turn),
        # The suite pins every workspace to the `local` adapter.
        patch(
            "loregarden.services.baxter_chat_service.resolve_effective_adapter",
            return_value="claude",
        ),
    ):
        res = client.post(
            f"{BASE}/{session_id}/messages",
            json={"content": "", "attachment_ids": [attachment["id"]]},
        )
    assert res.status_code == 202, res.text

    [request] = turn.requests
    image_dir = session_attachments_dir(session_id)
    assert request.extra_dirs == (image_dir,)
    assert str(image_dir / attachment["id"] / "shot.png") in request.prompt
    # A files-only turn still names the thread.
    assert client.get(f"{BASE}/{session_id}").json()["title"] == "shot.png"


def test_image_on_a_non_claude_adapter_is_refused_at_send(client: TestClient):
    session_id = _new_session(client)
    attachment = _upload(client, session_id, "shot.png", PNG, "image/png").json()

    with patch(
        "loregarden.services.baxter_chat_service.resolve_effective_adapter",
        return_value="cursor",
    ):
        res = client.post(
            f"{BASE}/{session_id}/messages",
            json={"content": "What is this?", "attachment_ids": [attachment["id"]]},
        )
    assert res.status_code == 400
    assert "runs 'cursor'" in res.json()["detail"]
    assert client.get(f"{BASE}/{session_id}").json()["messages"] == []


def test_unsupported_and_oversized_uploads_are_refused(client: TestClient):
    session_id = _new_session(client)
    assert _upload(client, session_id, "a.zip", b"PK\x03\x04", "application/zip").status_code == 400
    assert _upload(client, session_id, "empty.txt", b"", "text/plain").status_code == 400
    assert _upload(client, session_id, "bin.txt", b"\xff\xfe\x00", "text/plain").status_code == 400
    big = b"a" * (512 * 1024 + 1)
    assert _upload(client, session_id, "big.txt", big, "text/plain").status_code == 400


def test_unknown_attachment_id_is_refused_not_dropped(client: TestClient):
    session_id = _new_session(client)
    other = _new_session(client)
    foreign = _upload(client, other, "note.md", b"# hi", "text/markdown").json()

    for bad in (foreign["id"], "../../etc/passwd"):
        res = client.post(
            f"{BASE}/{session_id}/messages",
            json={"content": "look", "attachment_ids": [bad]},
        )
        assert res.status_code == 400, bad


def test_fork_copies_attachments_and_delete_removes_them(client: TestClient, monkeypatch):
    monkeypatch.setenv("LOREGARDEN_BAXTER_CHAT_STUB_RESPONSE", "ok")
    session_id = _new_session(client)
    attachment = _upload(client, session_id, "notes.md", b"# plan", "text/markdown").json()
    client.post(
        f"{BASE}/{session_id}/messages",
        json={"content": "see notes", "attachment_ids": [attachment["id"]]},
    )

    forked = client.post(f"{BASE}/{session_id}/fork", json={})
    assert forked.status_code == 201
    fork_id = forked.json()["id"]
    assert (session_attachments_dir(fork_id) / attachment["id"] / "notes.md").is_file()

    assert client.delete(f"{BASE}/{session_id}").status_code in (200, 204)
    assert not session_attachments_dir(session_id).exists()
    # The fork's copy is its own.
    assert (session_attachments_dir(fork_id) / attachment["id"] / "notes.md").is_file()


def test_attachment_is_served_back_with_a_locked_down_policy(client: TestClient):
    session_id = _new_session(client)
    attachment = _upload(client, session_id, "shot.png", PNG, "image/png").json()

    res = client.get(f"{BASE}/{session_id}/attachments/{attachment['id']}")
    assert res.status_code == 200
    assert res.content == PNG
    assert res.headers["content-type"] == "image/png"
    assert "default-src 'none'" in res.headers["content-security-policy"]

    other = _new_session(client)
    assert client.get(f"{BASE}/{other}/attachments/{attachment['id']}").status_code == 404
