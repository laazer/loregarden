"""Tests for chat UI-primitive parsing and ref resolution."""

from __future__ import annotations

import json
import re
import typing
from pathlib import Path

import pytest
from loregarden.agents.chat_role_prompt import chat_ui_primitives_blocks
from loregarden.models.domain import Ticket, WorkItemType, Workspace
from loregarden.models.domain.chat_primitives import (
    KNOWN_PRIMITIVES,
    BranchHistoryPart,
    ChatPart,
    CommitPart,
    EditPart,
    GiphyPart,
    QAPart,
    TextPart,
    ThinkingPart,
    TicketListPart,
    TicketPart,
    TodoListPart,
    WorkspacePart,
)
from loregarden.services.chat_primitives.parser import (
    parse_primitive_parts,
    parts_to_jsonable,
    reply_preview,
)
from loregarden.services.chat_primitives.resolver import resolve_parts
from loregarden.services.chat_primitives.storage import load_parts_json
from sqlmodel import Session, select


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


@pytest.mark.parametrize("kind", ["ticket_list", "status_column", "kanban", "filterable_kanban"])
def test_ticket_set_card_with_unknown_keys_stays_visible_text(kind: str):
    """A misspelled id list must not parse into the "every ticket" card.

    Baxter sent ``items`` instead of ``ticket_ids``; the key was dropped, and an
    empty ``ticket_ids`` renders every ticket in every workspace under the
    agent's title ("Blocking init-pre-tinkercg-3").
    """
    extra = ',"status":"blocked"' if kind == "status_column" else ""
    raw = (
        f'```loregarden\n{{"primitive":"{kind}","title":"Blocked"{extra},'
        '"items":[{"ticket_id":"6e954acb","note":"917"}]}\n```'
    )

    parts = parse_primitive_parts(raw)

    assert len(parts) == 1
    assert isinstance(parts[0], TextPart)
    assert parts[0].content == raw


def test_ticket_list_with_ticket_ids_parses():
    raw = '```loregarden\n{"primitive":"ticket_list","ticket_ids":["a","b"]}\n```'

    (part,) = parse_primitive_parts(raw)

    assert isinstance(part, TicketListPart)
    assert part.ticket_ids == ["a", "b"]


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


def test_todo_list_carries_plan_id():
    """The plan's identity across turns — the UI replaces the card keyed on it."""
    parts = parse_primitive_parts(
        '```loregarden\n{"primitive":"todo_list","owner":"agent","plan_id":"history-api",'
        '"title":"Agent execution plan","items":[{"id":"api","text":"Add API"}]}\n```'
    )

    assert len(parts) == 1
    part = parts[0]
    assert isinstance(part, TodoListPart)
    assert part.plan_id == "history-api"


def _make_ticket(session: Session, workspace_id: str, external_id: str, title: str) -> Ticket:
    ticket = Ticket(
        workspace_id=workspace_id,
        external_id=external_id,
        title=title,
        description="",
        work_item_type=WorkItemType.TASK,
    )
    session.add(ticket)
    session.commit()
    session.refresh(ticket)
    return ticket


def _other_workspace(session: Session) -> Workspace:
    workspace = Workspace(slug="other-ws", name="Other")
    session.add(workspace)
    session.commit()
    session.refresh(workspace)
    return workspace


def test_resolve_parts_fills_ticket_title(db_session: Session):
    workspace = db_session.exec(select(Workspace).where(Workspace.slug == "loregarden")).first()
    assert workspace is not None
    ticket = _make_ticket(db_session, workspace.id, "PRIM-1", "Primitive ticket")

    parts = resolve_parts(
        db_session,
        [TicketPart(ticket_id=ticket.external_id)],
        workspace_id=workspace.id,
    )
    assert isinstance(parts[0], TicketPart)
    assert parts[0].ticket_id == ticket.id
    assert parts[0].title == "Primitive ticket"


def test_resolve_parts_ignores_ticket_from_another_workspace(db_session: Session):
    """A chat is confined to one workspace, so a foreign uuid must not resolve."""
    other = _other_workspace(db_session)
    home = db_session.exec(select(Workspace).where(Workspace.slug == "loregarden")).first()
    assert home is not None
    foreign = _make_ticket(db_session, other.id, "PRIM-2", "Foreign ticket")

    parts = resolve_parts(
        db_session,
        [TicketPart(ticket_id=foreign.id)],
        workspace_id=home.id,
    )
    assert isinstance(parts[0], TicketPart)
    assert parts[0].ticket_id == foreign.id
    assert parts[0].title is None


def test_resolve_parts_picks_the_external_id_in_its_own_workspace(db_session: Session):
    """external_id is unique per workspace only, so the same ref exists in both."""
    other = _other_workspace(db_session)
    home = db_session.exec(select(Workspace).where(Workspace.slug == "loregarden")).first()
    assert home is not None
    mine = _make_ticket(db_session, home.id, "01-shared", "Mine")
    theirs = _make_ticket(db_session, other.id, "01-shared", "Theirs")

    parts = resolve_parts(db_session, [TicketPart(ticket_id="01-shared")], workspace_id=other.id)
    assert isinstance(parts[0], TicketPart)
    assert parts[0].ticket_id == theirs.id
    assert parts[0].ticket_id != mine.id
    assert parts[0].title == "Theirs"


def test_baxter_reply_persists_parts(client, monkeypatch):
    """Parts are stored with the message, so a reload still renders the card."""
    monkeypatch.setenv(
        "LOREGARDEN_BAXTER_CHAT_STUB_RESPONSE",
        'Here you go:\n```loregarden\n{"primitive":"thinking","content":"ok"}\n```\n',
    )
    session_id = client.post("/api/workspaces/loregarden/baxter-chat/sessions", json={}).json()[
        "id"
    ]
    res = client.post(
        f"/api/workspaces/loregarden/baxter-chat/sessions/{session_id}/messages",
        json={"content": "Show a thinking card"},
    )
    assert res.status_code == 202

    snapshot = client.get(f"/api/workspaces/loregarden/baxter-chat/sessions/{session_id}").json()
    parts = snapshot["messages"][-1]["parts"]
    assert any(p.get("primitive") == "thinking" for p in parts)


def test_stored_parts_survive_a_malformed_column():
    """A bad parts blob degrades to plain text; it must not break the thread."""
    assert load_parts_json("not json") == []
    assert load_parts_json('{"primitive":"text"}') == []
    assert load_parts_json(None) == []
    assert load_parts_json('[{"primitive":"text","content":"hi"},7]') == [
        {"primitive": "text", "content": "hi"}
    ]


def test_ticket_triage_reply_persists_parts(client, monkeypatch):
    """Ticket triage stored no parts at all before — the column was dead."""
    monkeypatch.setenv(
        "LOREGARDEN_TRIAGE_STUB_RESPONSE",
        'On it:\n```loregarden\n{"primitive":"thinking","content":"ok"}\n```\n',
    )
    ticket_id = client.get("/api/tickets").json()[0]["id"]
    sent = client.post(f"/api/tickets/{ticket_id}/triage/messages", json={"content": "status?"})
    assert sent.status_code in (200, 202)

    snapshot = client.get(f"/api/tickets/{ticket_id}/triage").json()
    assistant = [m for m in snapshot["messages"] if m["role"] == "assistant"][-1]
    assert any(p.get("primitive") == "thinking" for p in assistant["parts"])


def test_qa_fence_with_no_questions_stays_visible_text():
    """An empty `items` is a malformed fence, not a question.

    It used to parse, and `_agent_plan_reply_complete` treats any qa part as
    "needs the operator" — so a zero-item card parked plan execution behind a
    card that asked nothing, under a Send button that could never enable.
    Degrading to text keeps the reply readable and lets the plan loop continue.
    """
    raw = '```loregarden\n{"primitive":"qa","items":[]}\n```'

    parts = parse_primitive_parts(raw)

    assert len(parts) == 1
    assert isinstance(parts[0], TextPart)
    assert parts[0].content == raw


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        pytest.param(
            'Filed it:\n\n```loregarden\n{"primitive":"ticket","ticket_id":"t1"}\n```\n\nDone.',
            "Filed it: Done.",
            id="parsed-card-dropped",
        ),
        pytest.param(
            'Which?\n\n```loregarden\n{"primitive":"qa","question":"x","options":[]}\n```',
            "Which?",
            id="unparsed-card-dropped-too",
        ),
        pytest.param(
            '```loregarden\n{"primitive":"todo_list","owner":"agent","title":"Ship it",'
            '"items":[{"id":"a","text":"A"}]}\n```',
            "Ship it",
            id="card-only-named-by-title",
        ),
        pytest.param(
            '```loregarden\n{"primitive":"thinking","content":"hm"}\n```\n'
            '```loregarden\n{"primitive":"parent_ticket","ticket_id":"t1"}\n```',
            "Parent ticket card",
            id="card-only-named-by-kind-not-reasoning",
        ),
        pytest.param("", "", id="empty"),
    ],
)
def test_reply_preview_shows_prose_never_card_json(text: str, expected: str):
    assert reply_preview(text) == expected


_CLIENT_TYPES = (
    Path(__file__).resolve().parents[2] / "client/src/components/chat/primitives/types.ts"
)

#: The least each kind needs to be a card. Every kind the parser knows is here,
#: so a kind added without a way to parse it fails below rather than in a chat.
_MINIMAL_FENCES: dict[str, dict[str, object]] = {
    "text": {"content": "hi"},
    "thinking": {"content": "hmm"},
    "ticket": {"ticket_id": "t1"},
    "ticket_workflow": {"ticket_id": "t1"},
    "parent_ticket": {"ticket_id": "t1"},
    "ticket_list": {"ticket_ids": ["t1"]},
    "status_column": {"status": "backlog"},
    "kanban": {"statuses": ["backlog"]},
    "filterable_kanban": {"filters": ["mine"]},
    "agent": {"slug": "planner"},
    "workflow": {"workflow_slug": "default"},
    "gate": {"ticket_id": "t1", "stage_key": "verify"},
    "terminal": {"lines": [{"kind": "command", "text": "ls"}]},
    "edit": {"content": "x"},
    "calendar": {"events": [{"title": "Run", "starts_at": "2026-10-21T09:00:00Z"}]},
    "calendar_event": {"event": {"title": "Run", "starts_at": "2026-10-21T09:00:00Z"}},
    "workspace": {"workspace_slug": "loregarden"},
    "todo_list": {"items": [{"id": "a", "text": "Do it"}]},
    "branch_history": {"workspace_slug": "loregarden", "branch": "main"},
    "commit": {"workspace_slug": "loregarden", "sha": "HEAD"},
    "qa": {"items": [{"id": "q", "question": "Which?"}]},
    "btw": {"exchange_id": "e1", "ticket_id": "t1", "question": "Why?"},
    "giphy": {"giphy_id": "abc"},
}


def _union_kinds() -> set[str]:
    members = typing.get_args(typing.get_args(ChatPart)[0])
    return {member.model_fields["primitive"].default for member in members}


def test_every_kind_is_known_to_the_parser_the_union_and_the_client():
    client_union = re.search(r"export type PrimitiveKind =([^;]+);", _CLIENT_TYPES.read_text())
    assert client_union is not None
    client_kinds = set(re.findall(r'"(\w+)"', client_union.group(1)))

    assert set(_MINIMAL_FENCES) == KNOWN_PRIMITIVES
    assert _union_kinds() == KNOWN_PRIMITIVES
    assert client_kinds == KNOWN_PRIMITIVES


def test_the_prompt_teaches_every_kind_a_model_may_emit():
    """`text` is prose and `btw` is server-built; every other kind needs a name."""
    prompt = " ".join(chat_ui_primitives_blocks())
    listed = re.search(r"Kinds: ([^.]+)\.", prompt)
    assert listed is not None

    assert set(re.findall(r"\w+", listed.group(1))) == KNOWN_PRIMITIVES - {"text", "btw"}


@pytest.mark.parametrize("kind", sorted(_MINIMAL_FENCES))
def test_every_kind_parses_from_its_fence(kind: str):
    payload = json.dumps({"primitive": kind, **_MINIMAL_FENCES[kind]})

    (part,) = parse_primitive_parts(f"Lead in\n```loregarden\n{payload}\n```")[1:]

    assert part.model_dump()["primitive"] == kind


@pytest.mark.parametrize("kind", sorted(_MINIMAL_FENCES))
def test_every_kind_survives_a_dropped_final_brace(kind: str):
    """Models drop the last `}` of a card; the card is otherwise whole."""
    payload = json.dumps({"primitive": kind, **_MINIMAL_FENCES[kind]})

    intact = parse_primitive_parts(f"```loregarden\n{payload}\n```")

    assert parse_primitive_parts(f"```loregarden\n{payload[:-1]}\n```") == intact


def test_the_reported_qa_card_with_no_closing_brace_becomes_a_card():
    raw = (
        '```loregarden\n{"primitive":"qa","title":"Before I touch it","items":['
        '{"id":"split","question":"Split UI AC20\u2013AC30 into a child ticket?"},'
        '{"id":"cursor","question":"Why is the cursor at {plan} \\"synthesis\\"?"}]\n```'
    )

    (part,) = parse_primitive_parts(raw)

    assert isinstance(part, QAPart)
    assert [item.id for item in part.items] == ["split", "cursor"]
    assert part.items[1].question == 'Why is the cursor at {plan} "synthesis"?'


@pytest.mark.parametrize(
    "body",
    [
        pytest.param('{"primitive":"qa","items":[{"id":"q","question":"Whi', id="ends-in-a-string"),
        pytest.param('{"primitive":"ticket","ticket_id":"t1"]}', id="wrong-closer"),
        pytest.param('{"primitive":"ticket","ticket_id":"t1"}}', id="extra-closer"),
        pytest.param('{"primitive":"ticket","ticket_id":', id="ends-mid-pair"),
    ],
)
def test_a_fence_broken_any_other_way_stays_visible_text(body: str):
    raw = f"```loregarden\n{body}\n```"

    assert parse_primitive_parts(raw) == [TextPart(content=raw)]
