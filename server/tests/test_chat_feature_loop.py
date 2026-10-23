"""A chat's feature loop: Baxter proposes criteria, the operator decides, rounds run."""

from __future__ import annotations

import json
from unittest import mock

import pytest
from fastapi.testclient import TestClient
from loregarden.db.versions.baxter_chat_focus_and_feature_loop import (
    m_baxter_chat_focus_and_feature_loop,
)
from loregarden.mcp.tool_ids import AUTO_APPROVED_MCP_TOOLS, McpTool
from loregarden.mcp.tool_registry import EXTENDED_TOOLS
from loregarden.models.domain import (
    BaxterChatSession,
    FeatureCriterion,
    FeatureCriterionStatus,
    Ticket,
    TicketState,
)
from loregarden.services.acceptance_criteria import MAX_ACCEPTANCE_CRITERIA, load_criteria
from loregarden.services.chat_feature_loop import advance_feature_loops
from sqlalchemy import create_engine, text
from sqlmodel import Session, select

BASE = "/api/workspaces/loregarden/baxter-chat/sessions"


@pytest.fixture
def started_workflows():
    """Stand in for the orchestrator: record which tickets a round started."""
    with mock.patch("loregarden.services.chat_feature_loop._start_workflow") as start:
        yield start


@pytest.fixture
def loop(client: TestClient) -> dict:
    session_id = client.post(BASE, json={}).json()["id"]
    res = client.post(f"{BASE}/{session_id}/feature", json={"title": "Dark mode"})
    assert res.status_code == 200, res.text
    return res.json()


def _propose(db_session: Session, session_id: str, criteria: list[str]) -> dict:
    handler = EXTENDED_TOOLS[McpTool.PROPOSE_FEATURE_CRITERIA.value]
    return json.loads(handler(db_session, {"chat_session_id": session_id, "criteria": criteria}))


def _ticket(db_session: Session, snapshot: dict) -> Ticket:
    db_session.expire_all()
    ticket = db_session.get(Ticket, snapshot["feature"]["ticket"]["id"])
    assert ticket is not None
    return ticket


def _statuses(snapshot: dict) -> dict[str, str]:
    return {c["text"]: c["status"] for c in snapshot["feature"]["criteria"]}


def test_starting_a_loop_files_a_milestone_and_runs_nothing(loop: dict, started_workflows):
    feature = loop["feature"]

    assert feature["ticket"]["title"] == "Dark mode"
    assert feature["ticket"]["workspace_slug"] == "loregarden"
    assert feature["status"] == "idle"
    assert feature["criteria"] == []
    started_workflows.assert_not_called()


def test_proposals_wait_for_the_operator_and_skip_duplicates(
    client: TestClient, db_session: Session, loop: dict, started_workflows
):
    first = _propose(db_session, loop["id"], ["Toggle persists", "Toggle persists"])
    again = _propose(db_session, loop["id"], ["Toggle persists", "Respects OS setting"])

    snapshot = client.get(f"{BASE}/{loop['id']}").json()
    assert len(first["proposed"]) == 1
    assert [p["text"] for p in again["proposed"]] == ["Respects OS setting"]
    assert _statuses(snapshot) == {"Toggle persists": "proposed", "Respects OS setting": "proposed"}
    assert load_criteria(_ticket(db_session, snapshot).acceptance_criteria_json) == []
    started_workflows.assert_not_called()


def test_accepting_starts_a_round_and_dropping_does_not(
    client: TestClient, db_session: Session, loop: dict, started_workflows
):
    proposed = _propose(db_session, loop["id"], ["Toggle persists", "Ugly idea"])["proposed"]
    keep, drop = proposed[0]["id"], proposed[1]["id"]

    client.post(f"{BASE}/{loop['id']}/feature/decisions", json={"ids": [drop], "accept": False})
    res = client.post(
        f"{BASE}/{loop['id']}/feature/decisions", json={"ids": [keep], "accept": True}
    )

    assert res.status_code == 200, res.text
    snapshot = res.json()
    assert _statuses(snapshot) == {"Toggle persists": "in_round"}
    assert snapshot["feature"]["round"] == 1
    assert load_criteria(_ticket(db_session, snapshot).acceptance_criteria_json) == [
        "Toggle persists"
    ]
    started_workflows.assert_called_once()


def test_criteria_queued_mid_round_wait_then_run_on_a_reopened_ticket(
    client: TestClient, db_session: Session, loop: dict, started_workflows
):
    client.post(f"{BASE}/{loop['id']}/feature/criteria", json={"text": "Toggle persists"})
    with mock.patch("loregarden.services.chat_feature_loop.activity_for", return_value="running"):
        mid = client.post(f"{BASE}/{loop['id']}/feature/criteria", json={"text": "Respects OS"})
    assert _statuses(mid.json()) == {"Toggle persists": "in_round", "Respects OS": "queued"}

    ticket = _ticket(db_session, loop)
    ticket.state = TicketState.DONE
    db_session.add(ticket)
    db_session.commit()
    advance_feature_loops(db_session)

    snapshot = client.get(f"{BASE}/{loop['id']}").json()
    assert _statuses(snapshot) == {"Toggle persists": "met", "Respects OS": "in_round"}
    assert snapshot["feature"]["round"] == 2
    ticket = _ticket(db_session, snapshot)
    assert ticket.state != TicketState.DONE
    assert load_criteria(ticket.acceptance_criteria_json) == ["Toggle persists", "Respects OS"]
    assert started_workflows.call_count == 2


def test_a_blocked_ticket_holds_the_queue_and_says_why(
    client: TestClient, db_session: Session, loop: dict, started_workflows
):
    ticket = _ticket(db_session, loop)
    ticket.state = TicketState.BLOCKED
    ticket.blocking_issues = "Gate failed: lint"
    db_session.add(ticket)
    db_session.commit()

    snapshot = client.post(
        f"{BASE}/{loop['id']}/feature/criteria", json={"text": "Toggle persists"}
    ).json()

    assert snapshot["feature"]["status"] == "blocked"
    assert snapshot["feature"]["reason"] == "Gate failed: lint"
    assert _statuses(snapshot) == {"Toggle persists": "queued"}
    started_workflows.assert_not_called()


def test_accepting_past_the_criteria_cap_is_refused_before_anything_changes(
    client: TestClient, db_session: Session, loop: dict, started_workflows
):
    many = [f"Criterion {n}" for n in range(MAX_ACCEPTANCE_CRITERIA + 1)]
    ids = [p["id"] for p in _propose(db_session, loop["id"], many)["proposed"]]

    res = client.post(f"{BASE}/{loop['id']}/feature/decisions", json={"ids": ids, "accept": True})

    assert res.status_code == 400
    assert str(MAX_ACCEPTANCE_CRITERIA) in res.json()["detail"]
    db_session.expire_all()
    statuses = {
        row.status
        for row in db_session.exec(
            select(FeatureCriterion).where(FeatureCriterion.session_id == loop["id"])
        ).all()
    }
    assert statuses == {FeatureCriterionStatus.PROPOSED}
    started_workflows.assert_not_called()


def test_the_turn_is_told_its_loop_and_granted_only_the_proposal_tool(
    client: TestClient, db_session: Session, loop: dict, monkeypatch
):
    from loregarden.services import agent_turn_runner, baxter_chat_service

    captured: dict[str, object] = {}

    def fake_run(_profile, *, prompt, granted_tools=None, **_kwargs):
        captured["prompt"] = prompt
        captured["tools"] = list(granted_tools or [])
        return "ok"

    monkeypatch.delenv("LOREGARDEN_BAXTER_CHAT_STUB_RESPONSE", raising=False)
    monkeypatch.setattr(baxter_chat_service, "resolve_effective_adapter", lambda **_: "cursor")
    monkeypatch.setattr(
        baxter_chat_service, "get_agent", lambda _id: {"adapter": "cursor", "mcp_tools": []}
    )
    monkeypatch.setattr(agent_turn_runner, "run_cli_agent_turn", fake_run)

    client.post(f"{BASE}/{loop['id']}/messages", json={"content": "Add a toggle"})

    prompt = str(captured["prompt"])
    assert "## Feature loop" in prompt
    assert f"chat_session_id `{loop['id']}`" in prompt
    assert captured["tools"] == [McpTool.PROPOSE_FEATURE_CRITERIA.value]
    assert McpTool.PROPOSE_FEATURE_CRITERIA in AUTO_APPROVED_MCP_TOOLS


def test_deleting_a_looping_chat_keeps_its_ticket(
    client: TestClient, db_session: Session, loop: dict, started_workflows
):
    client.post(f"{BASE}/{loop['id']}/feature/criteria", json={"text": "Toggle persists"})
    ticket_id = loop["feature"]["ticket"]["id"]

    res = client.delete(f"{BASE}/{loop['id']}")

    assert res.status_code == 200, res.text
    db_session.expire_all()
    assert db_session.get(BaxterChatSession, loop["id"]) is None
    assert db_session.get(Ticket, ticket_id) is not None


def test_migration_is_idempotent_and_leaves_threads_without_a_loop():
    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE tickets (id TEXT PRIMARY KEY)"))
        conn.execute(text("CREATE TABLE baxter_chat_sessions (id TEXT PRIMARY KEY, title TEXT)"))
        conn.execute(text("INSERT INTO baxter_chat_sessions (id, title) VALUES ('old', 't')"))
    with engine.begin() as conn:
        m_baxter_chat_focus_and_feature_loop(conn)
        m_baxter_chat_focus_and_feature_loop(conn)
        row = conn.execute(
            text("SELECT feature_ticket_id FROM baxter_chat_sessions WHERE id = 'old'")
        ).one()
        focus = conn.execute(text("SELECT count(*) FROM baxter_chat_focus")).scalar()

    assert tuple(row) == (None,)
    assert focus == 0
