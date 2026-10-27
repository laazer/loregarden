"""The living map: filing records onto a chat's or a ticket's map, and reading it back."""

from __future__ import annotations

import json
from datetime import timedelta
from unittest import mock

import pytest
from loregarden.models.domain import (
    Approval,
    ApprovalKind,
    ApprovalStatus,
    BaxterChatMessage,
    BaxterChatSession,
    MapItemKind,
    MapItemState,
    MapScope,
    RunStatus,
)
from loregarden.models.domain.enums import utcnow
from loregarden.services.living_map.store import MapNotFound, living_map
from loregarden.services.memory_store import ObsidianMemoryStore
from loregarden.testing.factories import make_agent_run, make_ticket, make_workspace

T0 = utcnow() - timedelta(hours=2)


def _parts(*parts: dict) -> str:
    return json.dumps(list(parts))


@pytest.fixture
def chat(db_session):
    workspace = make_workspace(db_session, slug="lm")
    thread = BaxterChatSession(workspace_id=workspace.id, title="Plan the release")
    db_session.add(thread)
    db_session.commit()
    say = _sayer(db_session, thread.id)
    say(
        "assistant",
        0,
        {"primitive": "ticket", "ticket_id": "lg-x-1", "title": "Release checklist"},
        {
            "primitive": "todo_list",
            "plan_id": "plan-1",
            "items": [
                {"id": "a", "text": "Cut the branch", "checked": True},
                {"id": "b", "text": "Tag it"},
            ],
        },
        {"primitive": "qa", "items": [{"id": "q1", "question": "Ship on Friday?"}]},
    )
    say("user", 1)
    say(
        "assistant",
        2,
        {"primitive": "qa", "items": [{"id": "q2", "question": "Who announces it?"}]},
    )
    return thread, say


def _sayer(db_session, session_id: str):
    def say(role: str, minute: int, *parts: dict) -> BaxterChatMessage:
        message = BaxterChatMessage(
            session_id=session_id,
            role=role,
            content="…",
            parts_json=_parts(*parts),
            created_at=T0 + timedelta(minutes=minute),
        )
        db_session.add(message)
        db_session.commit()
        return message

    return say


def _rows(view):
    return [(item.seq, item.kind, item.state, item.title) for item in view.items]


def test_a_chat_map_files_its_topics_tasks_and_questions_in_order(db_session, chat):
    thread, _ = chat
    view = living_map(db_session, MapScope.CHAT, thread.id)

    assert _rows(view) == [
        (1, MapItemKind.TOPIC, MapItemState.OPEN, "Release checklist"),
        (2, MapItemKind.QUESTION, MapItemState.SETTLED, "Ship on Friday?"),
        (3, MapItemKind.TASK, MapItemState.SETTLED, "Cut the branch"),
        (4, MapItemKind.TASK, MapItemState.OPEN, "Tag it"),
        (5, MapItemKind.QUESTION, MapItemState.WAITING, "Who announces it?"),
    ]
    topic = view.items[0]
    # Everything after the ticket card hangs off it as its topic.
    assert {item.topic_id for item in view.items[1:]} == {topic.id}
    assert view.title == "Plan the release"
    assert [(s.name, s.count, s.ok) for s in view.sources] == [("chat parts", 5, True)]


def test_a_question_is_answered_by_the_reply_after_it(db_session, chat):
    thread, say = chat
    first = living_map(db_session, MapScope.CHAT, thread.id)
    assert first.items[-1].state == MapItemState.WAITING

    say("user", 3)
    after = living_map(db_session, MapScope.CHAT, thread.id)

    assert after.items[-1].state == MapItemState.SETTLED
    assert after.items[-1].seq == first.items[-1].seq


def test_filing_twice_files_nothing_new_and_changes_nothing(db_session, chat):
    thread, _ = chat
    first = living_map(db_session, MapScope.CHAT, thread.id)
    second = living_map(db_session, MapScope.CHAT, thread.id)

    assert [i.model_dump() for i in second.items] == [i.model_dump() for i in first.items]


def test_a_new_record_appends_at_the_tip_and_nothing_drawn_moves(db_session, chat):
    thread, say = chat
    before = living_map(db_session, MapScope.CHAT, thread.id)

    say(
        "assistant", 4, {"primitive": "qa", "items": [{"id": "q3", "question": "Draft the notes?"}]}
    )
    after = living_map(db_session, MapScope.CHAT, thread.id)

    assert len(after.items) == len(before.items) + 1
    assert [(i.id, i.seq) for i in after.items[:-1]] == [(i.id, i.seq) for i in before.items]
    assert after.items[-1].seq == before.items[-1].seq + 1
    assert after.items[-1].title == "Draft the notes?"


def test_a_record_from_earlier_still_appends_at_the_tip(db_session, chat):
    # Append-stable means by arrival, not by timestamp: a record that turns up
    # late with an early time must not push anything already drawn down.
    thread, say = chat
    before = living_map(db_session, MapScope.CHAT, thread.id)

    say("assistant", -30, {"primitive": "qa", "items": [{"id": "q0", "question": "Late arrival?"}]})
    after = living_map(db_session, MapScope.CHAT, thread.id)

    assert [(i.id, i.seq) for i in after.items[:-1]] == [(i.id, i.seq) for i in before.items]
    assert after.items[-1].title == "Late arrival?"


def test_a_plan_reemitted_under_its_id_updates_its_tasks_rather_than_adding_them(db_session, chat):
    thread, say = chat
    before = living_map(db_session, MapScope.CHAT, thread.id)

    say(
        "assistant",
        5,
        {
            "primitive": "todo_list",
            "plan_id": "plan-1",
            "items": [
                {"id": "a", "text": "Cut the branch", "checked": True},
                {"id": "b", "text": "Tag it", "checked": True},
            ],
        },
    )
    after = living_map(db_session, MapScope.CHAT, thread.id)

    assert len(after.items) == len(before.items)
    tag = next(i for i in after.items if i.title == "Tag it")
    assert tag.state == MapItemState.SETTLED
    assert tag.seq == next(i for i in before.items if i.title == "Tag it").seq


def test_an_unreadable_part_is_reported_not_dropped_quietly(db_session, chat):
    thread, say = chat
    say("assistant", 6, {"primitive": "qa", "items": []})

    view = living_map(db_session, MapScope.CHAT, thread.id)

    (report,) = view.sources
    assert report.ok is False
    assert "1 chat part(s) could not be read" in report.error


@pytest.fixture
def ticket(db_session, tmp_path):
    workspace = make_workspace(db_session, slug="lm")
    item = make_ticket(
        db_session, workspace_id=workspace.id, external_id="lm-map-1", title="Map me"
    )

    def approval(
        kind: ApprovalKind, status: ApprovalStatus, title: str, minute: int, stage: str
    ) -> None:
        db_session.add(
            Approval(
                ticket_id=item.id,
                workspace_id=workspace.id,
                kind=kind,
                status=status,
                title=title,
                stage_key=stage,
                created_at=T0 + timedelta(minutes=minute),
            )
        )
        db_session.commit()

    make_agent_run(
        db_session,
        workspace_id=workspace.id,
        ticket_id=item.id,
        run_code="RUN-7",
        agent_id="spec",
        stage_key="spec",
        status=RunStatus.SUCCEEDED,
        started_at=T0 + timedelta(minutes=1),
    )
    approval(ApprovalKind.CLI_QUESTION, ApprovalStatus.APPROVED, "Which schema?", 2, "spec")
    approval(ApprovalKind.CLI_PERMISSION, ApprovalStatus.APPROVED, "Run pytest", 3, "spec")
    approval(ApprovalKind.WORKFLOW_GATE, ApprovalStatus.PENDING, "Sign off the spec", 4, "spec")
    make_agent_run(
        db_session,
        workspace_id=workspace.id,
        ticket_id=item.id,
        run_code="RUN-8",
        agent_id="backend_implementer",
        stage_key="implement",
        status=RunStatus.FAILED,
        started_at=T0 + timedelta(minutes=5),
    )
    store = ObsidianMemoryStore(tmp_path)
    store.append_checkpoint(
        workspace_slug="lm", ticket_id="lm-map-1", run_id="RUN-7", entry="Assumed UTC everywhere"
    )
    return item, store


def test_a_ticket_map_files_stage_topics_runs_approvals_and_checkpoints(db_session, ticket):
    item, store = ticket
    with mock.patch.object(ObsidianMemoryStore, "from_settings", return_value=store):
        view = living_map(db_session, MapScope.TICKET, "lm-map-1")

    kinds = [(i.kind, i.state, i.title) for i in view.items]
    assert (MapItemKind.QUESTION, MapItemState.SETTLED, "Which schema?") in kinds
    assert (MapItemKind.WAIT, MapItemState.WAITING, "Sign off the spec") in kinds
    assert (MapItemKind.CHANGE, MapItemState.SETTLED, "spec ran spec") in kinds
    assert (MapItemKind.CHANGE, MapItemState.FAILED, "backend_implementer ran implement") in kinds
    assert (MapItemKind.ASSUMPTION, MapItemState.SETTLED, "Assumed UTC everywhere") in kinds
    # Tool-permission prompts are left off.
    assert all(i.title != "Run pytest" for i in view.items)
    topics = {i.id: i.title for i in view.items if i.kind == MapItemKind.TOPIC}
    assert sorted(topics.values()) == ["implement", "spec"]
    by_title = {i.title: i for i in view.items}
    # The checkpoint's run (RUN-7, a spec run) puts it under the spec stage.
    assert topics[by_title["Assumed UTC everywhere"].topic_id] == "spec"
    assert topics[by_title["Sign off the spec"].topic_id] == "spec"
    # Each topic comes before the items that hang off it.
    for i in view.items:
        if i.topic_id:
            assert next(t for t in view.items if t.id == i.topic_id).seq < i.seq
    assert [s.name for s in view.sources] == ["approvals", "agent runs", "checkpoints"]
    assert view.scope_id == item.id


def test_a_ticket_map_says_when_checkpoints_cannot_be_read(db_session, ticket):
    with mock.patch.object(ObsidianMemoryStore, "from_settings", return_value=None):
        view = living_map(db_session, MapScope.TICKET, "lm-map-1")

    report = next(s for s in view.sources if s.name == "checkpoints")
    assert report.ok is False
    assert "No Obsidian vault" in report.error
    assert all(i.kind != MapItemKind.ASSUMPTION for i in view.items)


def test_an_unknown_chat_or_ticket_is_not_found(db_session):
    with pytest.raises(MapNotFound):
        living_map(db_session, MapScope.CHAT, "no-such-chat")
    with pytest.raises(MapNotFound):
        living_map(db_session, MapScope.TICKET, "lm-none-404")


def test_the_api_serves_a_map_and_names_what_is_missing(client, db_session, chat):
    thread, _ = chat

    ok = client.get(f"/api/living-map/chat/{thread.id}")
    missing = client.get("/api/living-map/chat/no-such-chat")
    bad_scope = client.get("/api/living-map/nowhere/x")

    assert ok.status_code == 200
    assert [item["kind"] for item in ok.json()["items"]][:2] == ["topic", "question"]
    assert missing.status_code == 404
    assert "no-such-chat" in missing.json()["detail"]
    assert bad_scope.status_code == 422
