"""The 1-hop `memory_relations` digest in the briefing (lg-improved-memory-179)."""

from __future__ import annotations

import sqlite3
from unittest.mock import patch

import pytest
from loregarden.agents import inherited_wisdom
from loregarden.agents.inherited_wisdom import (
    MAX_RELATED_CHARS,
    MAX_RELATED_PER_LEARNING,
    build_inherited_wisdom,
)
from loregarden.services.memory_store import AgentMemoryService
from tests.memory_helpers import briefing_ticket


@pytest.fixture
def memory(tmp_path) -> AgentMemoryService:
    return AgentMemoryService(graph_sqlite_base=tmp_path / "memory.sqlite")


def _node(memory: AgentMemoryService, title: str, body: str = "unrelated body") -> str:
    return memory.upsert_memory(title=title, body=body, workspace_slug="lg")["graph"]["id"]


def _anchor(memory: AgentMemoryService) -> str:
    return _node(memory, "Rate limiting lesson", "Rate limiting on the public API.")


def _relate(memory, source, target, relation_type="related"):
    memory.create_relation(
        source_id=source, target_id=target, relation_type=relation_type, workspace_slug="lg"
    )


def _brief(memory):
    return build_inherited_wisdom(briefing_ticket(), "lg", memory=memory)


def test_digest_lists_neighbours_in_both_directions_by_title_and_type(memory):
    anchor = _anchor(memory)
    child = _node(memory, "Token bucket sizing", body="SECRET BODY TEXT")
    parent = _node(memory, "Older limiter note", body="SECRET BODY TEXT")
    _relate(memory, anchor, child, "refines")
    _relate(memory, parent, anchor, "supersedes")

    result = _brief(memory)

    assert "→ _refines_: Token bucket sizing" in result.text
    assert "← _supersedes_: Older limiter note" in result.text
    assert "SECRET BODY TEXT" not in result.text
    assert result.related_injected == 2


def test_a_densely_connected_node_cannot_bloat_the_briefing(memory):
    anchor = _anchor(memory)
    for index in range(60):
        _relate(memory, anchor, _node(memory, f"Neighbour {index} " + "x" * 200), "related")

    result = _brief(memory)

    digest = [line for line in result.text.splitlines() if line.startswith("  - ")]
    assert len(digest) == MAX_RELATED_PER_LEARNING
    assert sum(len(line) + 1 for line in digest) <= MAX_RELATED_CHARS


def test_the_total_digest_is_capped_across_learnings(memory):
    for index in range(5):
        anchor = _node(memory, f"Rate limiting lesson {index}", "Rate limiting on the public API.")
        for n in range(MAX_RELATED_PER_LEARNING):
            _relate(memory, anchor, _node(memory, f"N{index}-{n} " + "y" * 110))

    result = _brief(memory)

    digest = [line for line in result.text.splitlines() if line.startswith("  - ")]
    assert sum(len(line) + 1 for line in digest) <= MAX_RELATED_CHARS
    assert result.related_injected == len(digest)


def test_nodes_without_relations_add_nothing_and_write_nothing(memory, tmp_path):
    _anchor(memory)
    db = tmp_path / "lg" / "memory.sqlite"

    def counts():
        with sqlite3.connect(db) as conn:
            return tuple(
                conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]  # noqa: S608
                for t in ("memory_nodes", "memory_relations", "memory_node_versions")
            )

    before = counts()
    result = _brief(memory)

    assert result.related_injected == 0
    assert "  - " not in result.text
    assert counts() == before


def test_a_discredited_neighbour_is_not_surfaced(memory):
    anchor = _anchor(memory)
    bad = _node(memory, "Discredited neighbour")
    _relate(memory, anchor, bad)
    memory.set_discredited(
        node_id=bad, workspace_slug="lg", discredited=True, reason="wrong", writer="test"
    )

    assert "Discredited neighbour" not in _brief(memory).text


def test_neighbour_ranking_goes_through_the_one_swap_point(memory):
    anchor = _anchor(memory)
    for title in ("Alpha neighbour", "Beta neighbour"):
        _relate(memory, anchor, _node(memory, title))

    with patch.object(
        inherited_wisdom,
        "rank_related",
        side_effect=lambda rows, _conf: sorted(rows, key=lambda r: r["title"], reverse=True),
    ) as swap:
        text = _brief(memory).text

    assert swap.called
    assert text.index("Beta neighbour") < text.index("Alpha neighbour")
