"""Copy-on-write history for graph nodes: what an update replaces is kept."""

from __future__ import annotations

import sqlite3

import pytest
from loregarden.services.memory_history import HISTORY_KEEP_PER_NODE, ChangeAttribution
from loregarden.services.memory_store import (
    AgentMemoryService,
    MemoryGraphStore,
    MemoryNodeNotFoundError,
)
from tests.memory_helpers import frozen_clock


@pytest.fixture
def graph(tmp_path) -> MemoryGraphStore:
    return MemoryGraphStore(tmp_path / "memory.sqlite")


def _write(graph: MemoryGraphStore, body: str, **kwargs) -> dict:
    return graph.upsert_node(node_id="n1", title="Lesson", body=body, **kwargs)


def test_a_create_writes_no_history(graph):
    _write(graph, "first")

    assert graph.node_versions("n1") == []


def test_an_update_preserves_the_prior_version_before_overwriting(graph):
    with frozen_clock("2026-01-01T00:00:00+00:00", "2026-01-02T00:00:00+00:00"):
        _write(graph, "first", tags=["a"])
        _write(graph, "second", tags=["b"])

    (prior,) = graph.node_versions("n1")
    assert prior["version"] == 1
    assert prior["body"] == "first"
    assert prior["tags_json"] == '["a"]'
    assert prior["became_current_at"] == "2026-01-01T00:00:00+00:00"
    assert prior["superseded_at"] == "2026-01-02T00:00:00+00:00"
    assert graph.get_node("n1")["body"] == "second"


def test_repeated_updates_are_queryable_in_order(graph):
    for body in ("v1", "v2", "v3", "v4"):
        _write(graph, body)

    versions = graph.node_versions("n1")
    assert [v["version"] for v in versions] == [1, 2, 3]
    assert [v["body"] for v in versions] == ["v1", "v2", "v3"]


def test_an_identical_rewrite_supersedes_nothing(graph):
    _write(graph, "same")
    _write(graph, "same")

    assert graph.node_versions("n1") == []


def test_growth_is_bounded_and_version_numbers_are_never_reused(graph):
    total = HISTORY_KEEP_PER_NODE + 7
    for index in range(total + 1):
        _write(graph, f"v{index}")

    versions = graph.node_versions("n1")
    assert len(versions) == HISTORY_KEEP_PER_NODE
    # Oldest pruned, newest kept, and numbering continues past the prune.
    assert versions[0]["version"] == total - HISTORY_KEEP_PER_NODE + 1
    assert versions[-1]["version"] == total
    assert versions[-1]["body"] == f"v{total - 1}"


def test_prune_is_per_node(graph):
    for index in range(HISTORY_KEEP_PER_NODE + 3):
        _write(graph, f"v{index}")
    graph.upsert_node(node_id="n2", title="Other", body="a")
    graph.upsert_node(node_id="n2", title="Other", body="b")

    assert len(graph.node_versions("n2")) == 1
    assert len(graph.node_versions("n1")) == HISTORY_KEEP_PER_NODE


def test_unknown_attribution_is_null_not_an_empty_string(graph):
    _write(graph, "first")
    _write(graph, "second")

    with sqlite3.connect(graph.db_path) as conn:
        row = conn.execute("SELECT superseded_by, change_note FROM memory_node_versions").fetchone()
    assert row == (None, None)


def test_attribution_is_recorded_when_the_writer_gives_it(graph):
    _write(graph, "first")
    _write(graph, "first", discredited=True, attribution=ChangeAttribution("op", "wrong"))

    (prior,) = graph.node_versions("n1")
    assert prior["discredited"] is False
    assert (prior["superseded_by"], prior["change_note"]) == ("op", "wrong")


def test_live_reads_see_only_the_live_body(graph):
    _write(graph, "old wording about sqlite journals")
    _write(graph, "new wording")

    assert [n["body"] for n in graph.list_nodes()] == ["new wording"]
    assert graph.search("old wording") == []


def test_set_discredited_records_the_reason_and_restores(tmp_path):
    service = AgentMemoryService(graph_sqlite_base=tmp_path / "memory.sqlite")
    node = service.upsert_memory(title="Lesson", body="b", workspace_slug="ws")["graph"]

    marked = service.set_discredited(
        node_id=node["id"], workspace_slug="ws", discredited=True, reason="bad", writer="op"
    )
    assert marked["discredited"] is True
    assert marked["versions"][-1]["change_note"] == "bad"
    assert service.list_graph_nodes(workspace_slug="ws", limit=10, include_discredited=False) == []

    restored = service.set_discredited(
        node_id=node["id"], workspace_slug="ws", discredited=False, reason="ok", writer="op"
    )
    assert restored["discredited"] is False
    assert [v["discredited"] for v in restored["versions"]] == [False, True]


def test_set_discredited_on_a_missing_node_raises_not_found(tmp_path):
    service = AgentMemoryService(graph_sqlite_base=tmp_path / "memory.sqlite")
    with pytest.raises(MemoryNodeNotFoundError):
        service.set_discredited(
            node_id="nope", workspace_slug="ws", discredited=True, reason="r", writer="op"
        )
