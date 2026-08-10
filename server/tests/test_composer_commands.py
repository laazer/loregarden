"""Composer `/` commands: persisted post-it notes, and `/skill` on a chat turn."""

from fastapi.testclient import TestClient
from loregarden.models.domain import Workspace
from loregarden.services.baxter_chat_service import build_baxter_chat_prompt
from loregarden.skills.registry import list_skills
from sqlmodel import Session, select


def _new_chat(client: TestClient) -> str:
    res = client.post("/api/workspaces/loregarden/baxter-chat/sessions", json={})
    assert res.status_code == 201
    return res.json()["id"]


def test_note_round_trips_and_survives_a_fresh_read(client: TestClient):
    created = client.post(
        "/api/workspaces/loregarden/composer-notes", json={"body": "check the queue estimates"}
    )
    assert created.status_code == 201
    note = created.json()
    assert note["sent_at"] is None

    listed = client.get("/api/workspaces/loregarden/composer-notes").json()
    assert [item["id"] for item in listed] == [note["id"]]
    assert listed[0]["body"] == "check the queue estimates"


def test_sending_a_note_stamps_it_without_deleting_it(client: TestClient):
    note = client.post(
        "/api/workspaces/loregarden/composer-notes", json={"body": "ask about the lane"}
    ).json()

    sent = client.patch(
        f"/api/workspaces/loregarden/composer-notes/{note['id']}", json={"mark_sent": True}
    ).json()
    assert sent["sent_at"] is not None

    # A note is a draft you may send twice — sending must not remove it.
    assert len(client.get("/api/workspaces/loregarden/composer-notes").json()) == 1


def test_note_body_can_be_edited(client: TestClient):
    note = client.post("/api/workspaces/loregarden/composer-notes", json={"body": "first"}).json()
    updated = client.patch(
        f"/api/workspaces/loregarden/composer-notes/{note['id']}", json={"body": "second"}
    ).json()
    assert updated["body"] == "second"


def test_blank_note_is_rejected(client: TestClient):
    assert (
        client.post("/api/workspaces/loregarden/composer-notes", json={"body": "   "}).status_code
        == 400
    )


def test_note_is_scoped_to_its_workspace(client: TestClient, db_session: Session):
    db_session.add(Workspace(slug="other", name="Other", repo_path="."))
    db_session.commit()
    note = client.post(
        "/api/workspaces/loregarden/composer-notes", json={"body": "loregarden only"}
    ).json()

    assert client.get("/api/workspaces/other/composer-notes").json() == []
    assert client.delete(f"/api/workspaces/other/composer-notes/{note['id']}").status_code == 404


def test_note_deletion_removes_it(client: TestClient):
    note = client.post("/api/workspaces/loregarden/composer-notes", json={"body": "x"}).json()
    assert (
        client.delete(f"/api/workspaces/loregarden/composer-notes/{note['id']}").status_code == 200
    )
    assert client.get("/api/workspaces/loregarden/composer-notes").json() == []


def test_chat_turn_records_the_chosen_skill(client: TestClient, monkeypatch):
    monkeypatch.setenv("LOREGARDEN_BAXTER_CHAT_STUB_RESPONSE", "ok")
    skill = list_skills()[0]
    chat_id = _new_chat(client)

    res = client.post(
        f"/api/workspaces/loregarden/baxter-chat/sessions/{chat_id}/messages",
        json={"content": "review the diff", "skill": skill},
    )
    assert res.status_code == 202

    snapshot = client.get(f"/api/workspaces/loregarden/baxter-chat/sessions/{chat_id}").json()
    assert snapshot["messages"][0]["skill_name"] == skill
    # The assistant turn is what the choice produced, not where it is recorded.
    assert snapshot["messages"][1]["skill_name"] == ""


def test_unknown_skill_is_rejected_rather_than_ignored(client: TestClient, monkeypatch):
    monkeypatch.setenv("LOREGARDEN_BAXTER_CHAT_STUB_RESPONSE", "ok")
    chat_id = _new_chat(client)

    res = client.post(
        f"/api/workspaces/loregarden/baxter-chat/sessions/{chat_id}/messages",
        json={"content": "hello", "skill": "not-a-real-skill"},
    )
    assert res.status_code == 400
    assert "not-a-real-skill" in res.json()["detail"]


def test_chosen_skill_body_reaches_the_prompt(client: TestClient, db_session: Session):
    skill = list_skills()[0]
    workspace = db_session.exec(select(Workspace).where(Workspace.slug == "loregarden")).first()

    prompt = build_baxter_chat_prompt(
        workspace=workspace,
        history=[],
        latest_user_message="review the diff",
        approvals=[],
        tickets=[],
        skill_name=skill,
    )
    assert "## Skill" in prompt

    without = build_baxter_chat_prompt(
        workspace=workspace,
        history=[],
        latest_user_message="review the diff",
        approvals=[],
        tickets=[],
    )
    assert "## Skill" not in without
