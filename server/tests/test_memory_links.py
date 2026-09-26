"""Titles, aliases, typed edges and supersession for graph memory nodes."""

from __future__ import annotations

import pytest
from loregarden.agents.inherited_wisdom import MAX_RELATED_PER_LEARNING, build_inherited_wisdom
from loregarden.models.domain import MemoryRelationType
from loregarden.services.memory_links import (
    MemoryRelationError,
    learning_title,
    parse_relation_type,
)
from loregarden.services.memory_store import AgentMemoryService, ObsidianMemoryStore
from tests.memory_helpers import briefing_ticket, frozen_clock

WS = "lg"


@pytest.fixture
def memory(tmp_path) -> AgentMemoryService:
    return AgentMemoryService(graph_sqlite_base=tmp_path / "memory.sqlite")


def _node(memory, title, body="Rate limiting on the public API.", **kwargs) -> str:
    return memory.upsert_memory(title=title, body=body, workspace_slug=WS, **kwargs)["graph"]["id"]


# ---------------------------------------------------------------------------
# 1. Titles and aliases
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("content", "expected"),
    [
        ("Use DELETE journal on iCloud.\nWAL corrupts under sync.", "Use DELETE journal on iCloud"),
        ("\n\n## Token buckets beat sliding windows\n\nbody", "Token buckets beat sliding windows"),
        ("- Retry only idempotent calls", "Retry only idempotent calls"),
    ],
)
def test_a_learning_without_a_title_takes_its_first_line(content, expected):
    assert learning_title("", content) == (expected, True)


def test_a_long_first_line_is_cut_at_a_word_boundary():
    title, derived = learning_title("", "word " * 60)
    assert derived and title.endswith("…") and len(title) <= 91
    assert not title.removesuffix("…").endswith(" ")


def test_a_given_title_wins_and_empty_content_with_no_title_is_refused():
    assert learning_title("  Named   lesson ", "body") == ("Named lesson", False)
    with pytest.raises(ValueError):
        learning_title("", "  \n\n ")


def test_append_learning_no_longer_titles_by_ticket(memory):
    result = memory.append_learning(
        ticket_id="t-9", workspace_slug=WS, content="Pin the retry budget per stage.\nmore"
    )
    assert result["graph"]["title"] == "Pin the retry budget per stage"
    assert result["graph"]["ticket_id"] == "t-9"
    assert result["title_derived"] is True


def test_aliases_are_cleaned_kept_on_update_and_versioned(tmp_path):
    memory = AgentMemoryService(graph_sqlite_base=tmp_path / "memory.sqlite")
    node = memory.append_learning(
        ticket_id="t",
        workspace_slug=WS,
        content="body",
        title="Retrieval augmented generation",
        aliases=["RAG", " rag ", "Retrieval augmented generation", ""],
    )["graph"]
    assert node["aliases"] == ["RAG"]

    graph = memory._graph_for_workspace(WS)
    graph.upsert_node(node_id=node["id"], title=node["title"], body="new body", workspace_slug=WS)
    assert graph.get_node(node["id"])["aliases"] == ["RAG"]
    assert graph.node_versions(node["id"])[-1]["aliases"] == ["RAG"]


def test_recall_and_search_find_a_learning_by_its_alias(memory):
    memory.append_learning(
        ticket_id="t",
        workspace_slug=WS,
        content="Chunk pages before embedding them.",
        title="Retrieval augmented generation",
        aliases=["RAGPIPE"],
    )
    assert [r["title"] for r in memory.recall_related("ragpipe tuning", workspace_slug=WS)] == [
        "Retrieval augmented generation"
    ]
    assert len(memory.search("RAGPIPE", workspace_slug=WS)["graph"]) == 1


def test_exported_aliases_do_not_read_back_as_tags(tmp_path):
    vault = tmp_path / "vault"
    vault.mkdir()
    store = ObsidianMemoryStore(vault)
    note = store.upsert_note(title="T", body="b", tags=["x"], aliases=["Alias one"])
    read = store._read_note(vault / note.path)
    assert read.tags == ["x"]
    assert "aliases:\n  - Alias one" in (vault / note.path).read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# 2. Typed relations
# ---------------------------------------------------------------------------


def test_an_unknown_relation_type_is_refused_naming_the_vocabulary():
    with pytest.raises(MemoryRelationError, match="contradicts"):
        parse_relation_type("refines")
    assert parse_relation_type("supersedes") is MemoryRelationType.SUPERSEDES


def test_edges_need_two_real_distinct_nodes(memory):
    a = _node(memory, "A")
    with pytest.raises(MemoryRelationError, match="itself"):
        memory.create_relation(source_id=a, target_id=a, workspace_slug=WS)
    with pytest.raises(MemoryRelationError, match="nope"):
        memory.create_relation(source_id=a, target_id="nope", workspace_slug=WS)


def test_restating_an_edge_returns_it_rather_than_duplicating(memory):
    a, b = _node(memory, "A"), _node(memory, "B")
    first = memory.create_relation(
        source_id=a, target_id=b, relation_type=MemoryRelationType.SUPPORTS, workspace_slug=WS
    )
    again = memory.create_relation(
        source_id=a, target_id=b, relation_type=MemoryRelationType.SUPPORTS, workspace_slug=WS
    )
    assert (first["created"], again["created"], again["id"]) == (True, False, first["id"])


def test_a_contradiction_survives_the_digest_cap_and_is_flagged(memory):
    anchor = _node(memory, "Rate limiting lesson")
    # Written first, so recency alone would rank it last and the cap would drop it.
    with frozen_clock("2020-01-01T00:00:00+00:00"):
        against = _node(memory, "Limits belong at the gateway", body="x")
    memory.create_relation(
        source_id=against,
        target_id=anchor,
        relation_type=MemoryRelationType.CONTRADICTS,
        workspace_slug=WS,
    )
    for index in range(MAX_RELATED_PER_LEARNING + 2):
        memory.create_relation(
            source_id=anchor, target_id=_node(memory, f"Plain {index}", body="x"), workspace_slug=WS
        )

    text = build_inherited_wisdom(briefing_ticket(), WS, memory=memory).text

    assert "⚠ ← _contradicts_: Limits belong at the gateway" in text


# ---------------------------------------------------------------------------
# 3. Supersession
# ---------------------------------------------------------------------------


def _superseded_pair(memory):
    with frozen_clock("2026-01-01T00:00:00+00:00", "2026-02-01T00:00:00+00:00"):
        old = _node(memory, "Rate limits via nginx", body="Rate limiting on the public API: nginx.")
        new = _node(
            memory, "Rate limits via gateway", body="Rate limiting on the public API: gateway."
        )
    memory.create_relation(
        source_id=new, target_id=old, relation_type=MemoryRelationType.SUPERSEDES, workspace_slug=WS
    )
    return old, new


def test_recall_keeps_a_superseded_learning_but_after_its_successor(memory):
    old, new = _superseded_pair(memory)

    rows = memory.recall_related("rate limiting public", workspace_slug=WS)

    assert [r["id"] for r in rows] == [new, old]
    assert rows[1]["superseded_by"] == [{"id": new, "title": "Rate limits via gateway"}]
    assert rows[0]["superseded_by"] == []


def test_the_briefing_says_what_replaced_a_superseded_learning(memory):
    _superseded_pair(memory)

    text = build_inherited_wisdom(briefing_ticket(), WS, memory=memory).text

    assert "**Rate limits via nginx**" in text
    assert "_(superseded by Rate limits via gateway)_" in text
    assert text.index("Rate limits via gateway") < text.index("**Rate limits via nginx**")


def test_a_discredited_successor_does_not_supersede(memory):
    old, new = _superseded_pair(memory)
    memory.set_discredited(node_id=new, workspace_slug=WS, discredited=True, reason="r", writer="t")

    (row,) = memory.recall_related("rate limiting public", workspace_slug=WS)
    assert (row["id"], row["superseded_by"]) == (old, [])


def test_lineage_runs_oldest_first_through_the_chain(memory):
    old, mid = _superseded_pair(memory)
    newest = _node(memory, "Rate limits per tenant")
    memory.create_relation(
        source_id=newest,
        target_id=mid,
        relation_type=MemoryRelationType.SUPERSEDES,
        workspace_slug=WS,
    )

    for probe in (old, mid, newest):
        steps = memory.lineage(node_id=probe, workspace_slug=WS)
        assert [s["id"] for s in steps] == [old, mid, newest]


def test_lineage_stops_on_a_cycle(memory):
    a, b = _node(memory, "A"), _node(memory, "B")
    memory.create_relation(
        source_id=a, target_id=b, relation_type=MemoryRelationType.SUPERSEDES, workspace_slug=WS
    )
    memory.create_relation(
        source_id=b, target_id=a, relation_type=MemoryRelationType.SUPERSEDES, workspace_slug=WS
    )

    assert sorted(s["id"] for s in memory.lineage(node_id=a, workspace_slug=WS)) == sorted([a, b])


def test_node_detail_shows_every_edge_and_marks_discredited_ends(memory):
    old, new = _superseded_pair(memory)
    memory.set_discredited(node_id=old, workspace_slug=WS, discredited=True, reason="r", writer="t")

    detail = memory.node_detail(node_id=new, workspace_slug=WS)

    (edge,) = detail["relations"]
    assert (edge["relation_type"], edge["direction"], edge["node_id"], edge["discredited"]) == (
        "supersedes",
        "out",
        old,
        True,
    )
