"""Global Home Baxter threads: across every workspace, optionally on one initiative."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from loregarden.db.versions.baxter_chat_global_scope import m_baxter_chat_global_scope
from loregarden.mcp.tool_ids import INITIATIVE_PLANNER_MCP_TOOLS, mcp_tool_values
from loregarden.models.domain import BaxterChatScope, Ticket, TicketState, Workspace
from loregarden.models.domain.chat_primitives import TicketPart
from loregarden.models.domain.work_item_types import WorkItemType
from loregarden.services.chat_primitives.resolver import resolve_parts
from loregarden.testing.factories import make_ticket, make_workspace
from sqlalchemy import create_engine, text
from sqlmodel import Session, select

GLOBAL = "/api/baxter-chat/global/sessions"


def _workspace(session: Session, slug: str) -> Workspace:
    return session.exec(select(Workspace).where(Workspace.slug == slug)).one()


@pytest.fixture
def initiative(db_session: Session) -> Ticket:
    return make_ticket(
        db_session,
        workspace_id=None,
        external_id="init-1",
        title="Cross-repo launch",
        work_item_type=WorkItemType.INITIATIVE,
    )


@pytest.fixture
def other_workspace(db_session: Session, tmp_path, seeded_template) -> Workspace:
    """A second workspace that is checked out on this machine, as a real git repo."""
    repo = seeded_template.copy_repo(tmp_path / "other-repo")
    return make_workspace(db_session, slug="other", repo_path=str(repo))


@pytest.fixture
def captured_turn(monkeypatch) -> dict[str, object]:
    """Run turns on a fake CLI, as a triage agent granted only `get_ticket`.

    The narrow grant is what lets a test see which tools a turn *added*.
    """
    from loregarden.services import agent_turn_runner, baxter_chat_service

    captured: dict[str, object] = {}

    def fake_run(_profile, *, prompt, granted_tools=None, **_kwargs):
        captured["prompt"] = prompt
        captured["tools"] = list(granted_tools or [])
        return "ok"

    monkeypatch.delenv("LOREGARDEN_BAXTER_CHAT_STUB_RESPONSE", raising=False)
    monkeypatch.setattr(baxter_chat_service, "resolve_effective_adapter", lambda **_: "cursor")
    monkeypatch.setattr(
        baxter_chat_service,
        "get_agent",
        lambda _id: {"adapter": "cursor", "mcp_tools": ["loregarden_get_ticket"]},
    )
    monkeypatch.setattr(agent_turn_runner, "run_cli_agent_turn", fake_run)
    return captured


def _create(client: TestClient, initiative_id: str | None = None) -> dict:
    params = {"initiative_id": initiative_id} if initiative_id else {}
    res = client.post(GLOBAL, json={}, params=params)
    assert res.status_code == 201, res.text
    return res.json()


def test_a_global_thread_runs_in_a_checked_out_host(client: TestClient):
    snapshot = _create(client)

    assert snapshot["scope"] == BaxterChatScope.GLOBAL.value
    assert snapshot["initiative_id"] is None
    assert snapshot["host_workspace_slug"] == "loregarden"
    assert [row["id"] for row in client.get(GLOBAL).json()] == [snapshot["id"]]


def test_global_and_workspace_archives_do_not_leak_into_each_other(client: TestClient):
    global_id = _create(client)["id"]
    scoped = client.post("/api/workspaces/loregarden/baxter-chat/sessions", json={}).json()

    assert (
        client.get(f"/api/workspaces/loregarden/baxter-chat/sessions/{global_id}").status_code
        == 404
    )
    assert client.get(f"{GLOBAL}/{scoped['id']}").status_code == 404
    listed = client.get("/api/workspaces/loregarden/baxter-chat/sessions").json()
    assert [row["id"] for row in listed] == [scoped["id"]]


def test_an_initiative_focus_has_its_own_archive(client: TestClient, initiative: Ticket):
    unfocused = _create(client)["id"]
    focused = _create(client, initiative.id)

    assert focused["initiative_id"] == initiative.id
    assert [
        r["id"] for r in client.get(GLOBAL, params={"initiative_id": initiative.id}).json()
    ] == [focused["id"]]
    assert [r["id"] for r in client.get(GLOBAL).json()] == [unfocused]
    # Reading by id needs no focus: a link to a focused thread still opens.
    assert client.get(f"{GLOBAL}/{focused['id']}").status_code == 200


def test_a_workspace_route_refuses_an_initiative_focus(client: TestClient, initiative: Ticket):
    res = client.get(
        "/api/workspaces/loregarden/baxter-chat/sessions", params={"initiative_id": initiative.id}
    )

    assert res.status_code == 400


def test_an_unknown_initiative_is_a_404(client: TestClient):
    assert client.get(GLOBAL, params={"initiative_id": "nope"}).status_code == 404
    assert client.post(GLOBAL, json={}, params={"initiative_id": "nope"}).status_code == 404


def test_a_focused_thread_runs_in_its_initiatives_workspace(
    client: TestClient, db_session: Session, initiative: Ticket, other_workspace: Workspace
):
    make_ticket(
        db_session,
        workspace_id=other_workspace.id,
        external_id="ms-1",
        work_item_type=WorkItemType.MILESTONE,
        parent_ticket_id=initiative.id,
    )

    assert _create(client, initiative.id)["host_workspace_slug"] == "other"


def test_no_checked_out_workspace_is_a_409_naming_why(client: TestClient, db_session: Session):
    for workspace in db_session.exec(select(Workspace)).all():
        workspace.repo_path = "/nonexistent/loregarden-test-path"
        db_session.add(workspace)
    db_session.commit()

    res = client.post(GLOBAL, json={})

    assert res.status_code == 409
    assert "checked out" in res.json()["detail"]


def test_a_fork_stays_global_and_focused(client: TestClient, initiative: Ticket):
    source = _create(client, initiative.id)

    forked = client.post(f"{GLOBAL}/{source['id']}/fork", json={}).json()

    assert forked["scope"] == BaxterChatScope.GLOBAL.value
    assert forked["initiative_id"] == initiative.id


def test_a_global_turn_sees_every_workspace_and_the_initiatives_plan(
    client: TestClient,
    db_session: Session,
    initiative: Ticket,
    other_workspace: Workspace,
    captured_turn: dict[str, object],
):
    make_ticket(
        db_session,
        workspace_id=other_workspace.id,
        external_id="ms-1",
        title="Ship the other repo",
        work_item_type=WorkItemType.MILESTONE,
        parent_ticket_id=initiative.id,
    )
    elsewhere = make_ticket(
        db_session,
        workspace_id=_workspace(db_session, "loregarden").id,
        external_id="lg-77",
        title="Blocked in the control plane",
        state=TicketState.BLOCKED,
    )
    # Ahead of the seeded in-progress tickets, which otherwise fill the snapshot.
    elsewhere.priority = 0
    db_session.add(elsewhere)
    db_session.commit()
    session_id = _create(client, initiative.id)["id"]

    res = client.post(f"{GLOBAL}/{session_id}/messages", json={"content": "Where are we?"})

    assert res.status_code == 202, res.text
    prompt = str(captured_turn["prompt"])
    assert "Scope: every workspace" in prompt
    # The host is `other`, yet a ticket in `loregarden` is in the snapshot, labelled.
    assert (
        f"[blocked] p0 lg-77 (id {elsewhere.id}): Blocked in the control plane workspace=loregarden"
        in prompt
    )
    assert "## Focused initiative" in prompt
    assert "Cross-repo launch" in prompt
    assert "ms-1" in prompt
    tools = set(captured_turn["tools"])
    assert {"loregarden_get_ticket", *mcp_tool_values(INITIATIVE_PLANNER_MCP_TOOLS)} <= tools


def test_a_workspace_turn_is_still_confined_to_its_workspace(
    client: TestClient,
    db_session: Session,
    other_workspace: Workspace,
    captured_turn: dict[str, object],
):
    make_ticket(
        db_session,
        workspace_id=other_workspace.id,
        external_id="other-9",
        state=TicketState.BLOCKED,
    )
    scoped = client.post("/api/workspaces/loregarden/baxter-chat/sessions", json={}).json()

    client.post(
        f"/api/workspaces/loregarden/baxter-chat/sessions/{scoped['id']}/messages",
        json={"content": "Where are we?"},
    )

    prompt = str(captured_turn["prompt"])
    assert "Workspace: loregarden" in prompt
    assert "other-9" not in prompt
    assert "## Focused initiative" not in prompt
    assert captured_turn["tools"] == ["loregarden_get_ticket"]


def test_global_refs_resolve_a_uuid_in_any_workspace_but_not_an_external_id(
    db_session: Session, other_workspace: Workspace
):
    ticket = make_ticket(db_session, workspace_id=other_workspace.id, external_id="other-3")

    by_uuid, by_external = resolve_parts(
        db_session,
        [TicketPart(ticket_id=ticket.id), TicketPart(ticket_id="other-3")],
        workspace_id=None,
    )

    assert by_uuid.title == ticket.title
    # External ids repeat across workspaces, so a global chat must not guess one.
    assert by_external.ticket_id == "other-3"
    assert by_external.title is None


def test_migration_adds_scope_to_existing_threads():
    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        conn.execute(
            text(
                "CREATE TABLE baxter_chat_sessions (id TEXT PRIMARY KEY, workspace_id TEXT "
                "NOT NULL, title TEXT NOT NULL DEFAULT '', runtime_json TEXT NOT NULL DEFAULT '{}')"
            )
        )
        conn.execute(
            text("INSERT INTO baxter_chat_sessions (id, workspace_id) VALUES ('old', 'w')")
        )
    with engine.begin() as conn:
        m_baxter_chat_global_scope(conn)
        m_baxter_chat_global_scope(conn)
        row = conn.execute(
            text("SELECT scope, initiative_id FROM baxter_chat_sessions WHERE id = 'old'")
        ).one()

    assert tuple(row) == ("workspace", None)
