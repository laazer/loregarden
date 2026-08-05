"""Home Baxter chat API — persisted sessions, turns, and the approval bridge."""

from unittest.mock import MagicMock

from fastapi.testclient import TestClient
from loregarden.agents.executors.permission_bridge import (
    HOME_CHAT_STAGE_KEY,
    BridgeResult,
)
from loregarden.models.domain import AgentRun, RunStatus, Workspace
from sqlmodel import Session, select


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


def test_baxter_chat_runtime_round_trips_on_session(client: TestClient):
    session_id = _new_session(client)

    saved = client.patch(
        f"/api/workspaces/loregarden/baxter-chat/sessions/{session_id}/runtime",
        json={
            "cli_adapter": "cursor",
            "claude_model": "",
            "cursor_model": "gpt-5",
            "codex_model": "",
            "lmstudio_base_url": "",
            "lmstudio_model": "",
            "claude_effort": "",
            "cursor_effort": "",
            "lmstudio_effort": "",
        },
    )

    assert saved.status_code == 200
    assert saved.json()["cli_adapter"] == "cursor"
    assert saved.json()["cursor_model"] == "gpt-5"

    snapshot = client.get(f"/api/workspaces/loregarden/baxter-chat/sessions/{session_id}").json()
    assert snapshot["runtime"]["cli_adapter"] == "cursor"
    assert snapshot["runtime"]["cursor_model"] == "gpt-5"


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
    # Force the advisory path so this test stays about prompt content, not the bridge.
    monkeypatch.setattr(baxter_chat_service, "resolve_effective_adapter", lambda **_: "cursor")
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
    assert "Never invent ticket/agent ids" in prompt
    assert "Agent execution plan" in prompt
    assert '"primitive":"todo_list"' in prompt
    assert "emit `qa`" in prompt
    # History comes from the stored thread, not from the client.
    assert "user: Hi" in prompt
    assert "assistant: Hello" in prompt
    # The read-only turn is told its snapshot is advisory, not actionable.
    assert "advisory only" in prompt


def test_baxter_chat_uses_session_runtime_for_turn(client: TestClient, monkeypatch):
    from loregarden.services import baxter_chat_service

    captured: dict[str, object] = {}

    def fake_run(_profile, *, workspace, **_kwargs):
        captured["adapter"] = workspace.cli_adapter
        captured["cursor_model"] = workspace.cursor_model
        return "ok from selected runtime"

    monkeypatch.delenv("LOREGARDEN_BAXTER_CHAT_STUB_RESPONSE", raising=False)
    monkeypatch.setattr(baxter_chat_service, "run_cli_agent_turn", fake_run)

    session_id = _new_session(client)
    client.patch(
        f"/api/workspaces/loregarden/baxter-chat/sessions/{session_id}/runtime",
        json={
            "cli_adapter": "cursor",
            "claude_model": "",
            "cursor_model": "gpt-5",
            "codex_model": "",
            "lmstudio_base_url": "",
            "lmstudio_model": "",
            "claude_effort": "",
            "cursor_effort": "",
            "lmstudio_effort": "",
        },
    )
    res = client.post(
        f"/api/workspaces/loregarden/baxter-chat/sessions/{session_id}/messages",
        json={"content": "Which runtime?"},
    )

    assert res.status_code == 202
    assert captured == {"adapter": "cursor", "cursor_model": "gpt-5"}


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


def test_stop_settles_pending_turn_and_unlocks_composer(client: TestClient):
    from loregarden.db.session import engine
    from loregarden.models.domain import BaxterChatMessage
    from sqlmodel import Session

    session_id = _new_session(client)
    with Session(engine) as db:
        db.add(BaxterChatMessage(session_id=session_id, role="assistant", status="pending"))
        db.commit()

    assert (
        client.get(f"/api/workspaces/loregarden/baxter-chat/sessions/{session_id}").json()[
            "run_status"
        ]
        == "running"
    )

    res = client.post(f"/api/workspaces/loregarden/baxter-chat/sessions/{session_id}/stop")
    assert res.status_code == 200
    body = res.json()
    assert body["run_status"] == "idle"
    assert body["active_turn_id"] is None
    assert "stopped" in body["messages"][-1]["content"].lower()

    # Idempotent refusal once nothing is in flight.
    again = client.post(f"/api/workspaces/loregarden/baxter-chat/sessions/{session_id}/stop")
    assert again.status_code == 409


def test_stop_does_not_let_late_complete_overwrite_cancelled_turn(client: TestClient):
    from loregarden.db.session import engine
    from loregarden.models.domain import BaxterChatMessage
    from loregarden.services.baxter_chat_run_service import _settle
    from sqlmodel import Session

    session_id = _new_session(client)
    with Session(engine) as db:
        pending = BaxterChatMessage(session_id=session_id, role="assistant", status="pending")
        db.add(pending)
        db.commit()
        db.refresh(pending)
        pending_id = pending.id

    assert (
        client.post(
            f"/api/workspaces/loregarden/baxter-chat/sessions/{session_id}/stop"
        ).status_code
        == 200
    )

    with Session(engine) as db:
        late = _settle(db, pending_id, content="I finished anyway", status="complete")
        assert late is not None
        assert late.status == "failed"
        assert "stopped" in late.content.lower()


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


def test_baxter_chat_interactive_prompt_grants_tools(client: TestClient, monkeypatch):
    from loregarden.services import baxter_chat_service

    captured: dict[str, object] = {}

    def fake_interactive(session, workspace, prompt, *, agent, turn_id=""):
        captured["prompt"] = prompt
        captured["workspace"] = workspace.slug
        captured["turn_id"] = turn_id
        return "patched the flaky test"

    monkeypatch.delenv("LOREGARDEN_BAXTER_CHAT_STUB_RESPONSE", raising=False)
    monkeypatch.setattr(baxter_chat_service, "resolve_effective_adapter", lambda **_: "claude")
    monkeypatch.setattr(baxter_chat_service, "_run_interactive_turn", fake_interactive)

    session_id = _new_session(client)
    res = client.post(
        f"/api/workspaces/loregarden/baxter-chat/sessions/{session_id}/messages",
        json={"content": "Fix the flaky suite"},
    )
    assert res.status_code == 202

    snapshot = client.get(f"/api/workspaces/loregarden/baxter-chat/sessions/{session_id}").json()
    assert snapshot["messages"][-1]["content"] == "patched the flaky test"
    prompt = str(captured["prompt"])
    assert "real tool access" in prompt
    assert "no ticket is implied" in prompt
    # The pending assistant row's id, so the turn's reasoning has a channel.
    assert captured["turn_id"] == snapshot["messages"][-1]["id"]


def test_baxter_chat_interactive_creates_workspace_scoped_run(
    client: TestClient, monkeypatch, db_session: Session
):
    from loregarden.services import baxter_chat_service

    monkeypatch.delenv("LOREGARDEN_BAXTER_CHAT_STUB_RESPONSE", raising=False)
    monkeypatch.setattr(baxter_chat_service, "resolve_effective_adapter", lambda **_: "claude")
    monkeypatch.setattr(
        baxter_chat_service,
        "build_interactive_invocation",
        lambda **_: MagicMock(adapter="claude", argv=["claude"], cwd="/tmp", resume_session_id=""),
    )
    monkeypatch.setattr(
        baxter_chat_service,
        "resolve_workspace_root",
        lambda _ws: MagicMock(is_dir=lambda: True),
    )

    def fake_bridge_run(self, **kwargs):
        assert kwargs["workspace"].slug == "loregarden"
        assert kwargs.get("ticket") is None
        run = db_session.get(AgentRun, kwargs["run_id"])
        assert run is not None
        assert run.ticket_id is None
        assert run.stage_key == HOME_CHAT_STAGE_KEY
        assert run.status == RunStatus.RUNNING
        return BridgeResult(status=RunStatus.SUCCEEDED, stdout="done", stderr="")

    monkeypatch.setattr(baxter_chat_service.PermissionBridgeRunner, "run", fake_bridge_run)
    monkeypatch.setattr(baxter_chat_service, "extract_triage_reply", lambda stdout: stdout)

    session_id = _new_session(client)
    res = client.post(
        f"/api/workspaces/loregarden/baxter-chat/sessions/{session_id}/messages",
        json={"content": "Investigate the inbox"},
    )
    assert res.status_code == 202

    snapshot = client.get(f"/api/workspaces/loregarden/baxter-chat/sessions/{session_id}").json()
    assert snapshot["messages"][-1]["content"] == "done"

    workspace = db_session.exec(select(Workspace).where(Workspace.slug == "loregarden")).first()
    finished = db_session.exec(
        select(AgentRun).where(
            AgentRun.workspace_id == workspace.id,
            AgentRun.stage_key == HOME_CHAT_STAGE_KEY,
            AgentRun.status == RunStatus.SUCCEEDED,
        )
    ).all()
    assert finished
    assert finished[-1].ticket_id is None
    assert finished[-1].agent_id == "triage"


def test_baxter_chat_conflict_when_turn_already_running(
    client: TestClient, monkeypatch, db_session: Session
):
    from loregarden.services import baxter_chat_service

    workspace = db_session.exec(select(Workspace).where(Workspace.slug == "loregarden")).first()
    db_session.add(
        AgentRun(
            run_code="run_busy",
            ticket_id=None,
            workspace_id=workspace.id,
            agent_id="triage",
            stage_key=HOME_CHAT_STAGE_KEY,
            status=RunStatus.RUNNING,
        )
    )
    db_session.commit()

    monkeypatch.delenv("LOREGARDEN_BAXTER_CHAT_STUB_RESPONSE", raising=False)
    monkeypatch.setattr(baxter_chat_service, "resolve_effective_adapter", lambda **_: "claude")

    session_id = _new_session(client)
    res = client.post(
        f"/api/workspaces/loregarden/baxter-chat/sessions/{session_id}/messages",
        json={"content": "Another question"},
    )
    # Accepted onto the thread — a fresh thread has no pending turn of its own,
    # so the per-session guard does not fire and this is not a 409.
    assert res.status_code == 202

    # The workspace-scoped run check then rejects it inside the turn, which
    # surfaces as a failed assistant message rather than a rejected request.
    snapshot = client.get(f"/api/workspaces/loregarden/baxter-chat/sessions/{session_id}").json()
    assert "previous message" in snapshot["messages"][-1]["content"].lower()


def test_home_chat_snapshot_surfaces_pending_approvals_and_awaiting_input(
    client: TestClient, db_session: Session
):
    """AskUserQuestion during a Home turn must ride on the session, not only the board."""
    import json

    from loregarden.models.domain import Approval, ApprovalKind, ApprovalStatus, BaxterChatMessage

    session_id = _new_session(client)
    workspace = db_session.exec(select(Workspace).where(Workspace.slug == "loregarden")).first()
    run = AgentRun(
        run_code="run_home_ask",
        ticket_id=None,
        workspace_id=workspace.id,
        agent_id="triage",
        stage_key=HOME_CHAT_STAGE_KEY,
        status=RunStatus.AWAITING_PERMISSION,
    )
    db_session.add(run)
    db_session.commit()
    db_session.refresh(run)

    db_session.add(
        BaxterChatMessage(
            session_id=session_id, role="user", content="Ship queue history?", status="complete"
        )
    )
    db_session.add(BaxterChatMessage(session_id=session_id, role="assistant", status="pending"))
    db_session.add(
        Approval(
            ticket_id=None,
            workspace_id=workspace.id,
            run_id=run.id,
            kind=ApprovalKind.CLI_QUESTION,
            title="Which shape?",
            stage_key=HOME_CHAT_STAGE_KEY,
            tool_name="AskUserQuestion",
            tool_input_json=json.dumps(
                {
                    "questions": [
                        {
                            "question": "Cards or table?",
                            "header": "Shape",
                            "options": [{"label": "Cards"}, {"label": "Table"}],
                        }
                    ]
                }
            ),
            status=ApprovalStatus.PENDING,
        )
    )
    db_session.commit()

    snapshot = client.get(f"/api/workspaces/loregarden/baxter-chat/sessions/{session_id}").json()
    assert snapshot["run_status"] == "awaiting_input"
    assert len(snapshot["pending_approvals"]) == 1
    assert snapshot["pending_approvals"][0]["title"] == "Which shape?"
    assert snapshot["pending_approvals"][0]["kind"] == "cli_question"

    idle = _new_session(client)
    other = client.get(f"/api/workspaces/loregarden/baxter-chat/sessions/{idle}").json()
    assert other["pending_approvals"] == []
    assert other["run_status"] == "idle"
