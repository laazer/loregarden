"""Tests for chat UI-primitive parsing and ref resolution."""

from __future__ import annotations

import pytest
from loregarden.models.domain.chat_primitives import (
    BranchHistoryPart,
    CommitPart,
    EditPart,
    GiphyPart,
    QAPart,
    TextPart,
    ThinkingPart,
    TicketPart,
    TodoListPart,
    WorkspacePart,
)
from loregarden.services.chat_primitives.parser import parse_primitive_parts, parts_to_jsonable
from loregarden.services.chat_primitives.resolver import resolve_parts
from sqlmodel import Session


def test_plain_text_is_a_single_text_part():
    parts = parse_primitive_parts("Hello Baxter")
    assert len(parts) == 1
    assert isinstance(parts[0], TextPart)
    assert parts[0].content == "Hello Baxter"


def test_valid_ticket_fence_parses():
    text = (
        'Here is the ticket:\n\n```loregarden\n{"primitive":"ticket","ticket_id":"abc-123"}\n```\n'
    )
    parts = parse_primitive_parts(text)
    assert len(parts) == 2
    assert isinstance(parts[0], TextPart)
    assert "Here is the ticket" in parts[0].content
    assert isinstance(parts[1], TicketPart)
    assert parts[1].ticket_id == "abc-123"


def test_thinking_fence_parses():
    text = '```loregarden\n{"primitive":"thinking","content":"hmm"}\n```'
    parts = parse_primitive_parts(text)
    assert len(parts) == 1
    assert isinstance(parts[0], ThinkingPart)
    assert parts[0].content == "hmm"
    assert parts[0].collapsed is True


def test_malformed_json_degrades_to_text():
    fence = "```loregarden\n{not json}\n```"
    parts = parse_primitive_parts(f"Before\n{fence}\nAfter")
    assert any(isinstance(p, TextPart) and fence in p.content for p in parts)
    assert not any(isinstance(p, TicketPart) for p in parts)


def test_unknown_primitive_kind_degrades_to_text():
    fence = '```loregarden\n{"primitive":"spaceship","fuel":1}\n```'
    parts = parse_primitive_parts(fence)
    assert len(parts) == 1
    assert isinstance(parts[0], TextPart)
    assert "spaceship" in parts[0].content


def test_invalid_schema_degrades_to_text():
    # ticket requires ticket_id
    fence = '```loregarden\n{"primitive":"ticket"}\n```'
    parts = parse_primitive_parts(fence)
    assert len(parts) == 1
    assert isinstance(parts[0], TextPart)


def test_parts_to_jsonable_round_trips():
    parts = parse_primitive_parts(
        '```loregarden\n{"primitive":"ticket","ticket_id":"t1","title":"X"}\n```'
    )
    payload = parts_to_jsonable(parts)
    assert payload == [{"primitive": "ticket", "ticket_id": "t1", "title": "X"}]


@pytest.mark.parametrize(
    ("payload", "part_type"),
    [
        (
            '{"primitive":"workspace","workspace_slug":"loregarden"}',
            WorkspacePart,
        ),
        (
            '{"primitive":"todo_list","owner":"user","items":[{"id":"1","text":"Review"}]}',
            TodoListPart,
        ),
        (
            '{"primitive":"branch_history","workspace_slug":"loregarden","branch":"main"}',
            BranchHistoryPart,
        ),
        (
            '{"primitive":"commit","workspace_slug":"loregarden","sha":"HEAD"}',
            CommitPart,
        ),
        (
            '{"primitive":"qa","items":[{"id":"scope","question":"Who is this for?"}]}',
            QAPart,
        ),
        (
            '{"primitive":"giphy","giphy_id":"ICOgUNjpvO0PC"}',
            GiphyPart,
        ),
        (
            '{"primitive":"edit","path":"a.md","original":"old\\n","content":"new\\n","title":"Patch"}',
            EditPart,
        ),
    ],
)
def test_new_primitive_fences_parse(payload, part_type):
    parts = parse_primitive_parts(f"```loregarden\n{payload}\n```")

    assert len(parts) == 1
    assert isinstance(parts[0], part_type)
    if isinstance(parts[0], EditPart):
        assert parts[0].original == "old\n"
        assert parts[0].content == "new\n"
        assert parts[0].path == "a.md"


def test_resolve_parts_fills_ticket_title(db_session: Session):
    from loregarden.models.domain import Ticket, WorkItemType, Workspace
    from sqlmodel import select

    workspace = db_session.exec(select(Workspace).where(Workspace.slug == "loregarden")).first()
    assert workspace is not None
    ticket = Ticket(
        workspace_id=workspace.id,
        external_id="PRIM-1",
        title="Primitive ticket",
        description="",
        work_item_type=WorkItemType.TASK,
    )
    db_session.add(ticket)
    db_session.commit()
    db_session.refresh(ticket)

    parts = resolve_parts(
        db_session,
        [TicketPart(ticket_id=ticket.external_id)],
    )
    assert isinstance(parts[0], TicketPart)
    assert parts[0].ticket_id == ticket.id
    assert parts[0].title == "Primitive ticket"


def test_baxter_reply_includes_parts(client, monkeypatch):
    monkeypatch.setenv(
        "LOREGARDEN_BAXTER_CHAT_STUB_RESPONSE",
        'Here you go:\n```loregarden\n{"primitive":"thinking","content":"ok"}\n```\n',
    )
    res = client.post(
        "/api/workspaces/loregarden/baxter-chat/messages",
        json={"content": "Show a thinking card", "history": []},
    )
    assert res.status_code == 200
    body = res.json()
    assert "reply" in body
    assert isinstance(body["parts"], list)
    assert any(p.get("primitive") == "thinking" for p in body["parts"])
