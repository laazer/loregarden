"""The knowledge browser's read API and node provenance (766)."""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest
from loregarden.models.domain import MemoryOriginKind, MemoryRelationType
from loregarden.services.memory_store import AgentMemoryService
from tests.memory_helpers import frozen_clock

WS = "kb"


@pytest.fixture
def memory() -> AgentMemoryService:
    return AgentMemoryService.from_settings()


@pytest.fixture
def three_nodes(memory) -> dict[str, str]:
    """Three learnings, oldest first, with an edge from the newest to the oldest
    and one between the two newest."""
    ids = {}
    with frozen_clock(
        "2026-01-01T00:00:00+00:00", "2026-01-02T00:00:00+00:00", "2026-01-03T00:00:00+00:00"
    ):
        for name in ("old", "mid", "new"):
            ids[name] = memory.upsert_memory(
                title=f"{name} lesson", body=f"body of {name}", workspace_slug=WS
            )["graph"]["id"]
    memory.create_relation(source_id=ids["new"], target_id=ids["old"], workspace_slug=WS)
    memory.create_relation(
        source_id=ids["new"],
        target_id=ids["mid"],
        relation_type=MemoryRelationType.SUPPORTS,
        workspace_slug=WS,
    )
    return ids


def _graph(client, **params):
    response = client.get("/api/memory/graph", params={"workspace_slug": WS, **params})
    assert response.status_code == 200, response.text
    return response.json()


def test_the_graph_lists_nodes_and_the_edges_between_them(client, three_nodes):
    graph = _graph(client)

    assert graph["configured"] is True
    assert graph["source"] == "list"
    assert {n["id"] for n in graph["nodes"]} == set(three_nodes.values())
    assert graph["counts"] == {"entities": 3, "links": 2}
    assert graph["type_counts"] == {"memory": 3}


def test_an_edge_to_a_node_outside_the_window_is_not_returned(client, three_nodes):
    # Nodes list newest first, so limit=2 drops "old" — and the edge to it.
    graph = _graph(client, limit=2)

    returned = {n["id"] for n in graph["nodes"]}
    assert three_nodes["old"] not in returned
    assert all(
        r["source_id"] in returned and r["target_id"] in returned for r in graph["relations"]
    )
    assert graph["counts"]["links"] == len(graph["relations"])
    assert graph["truncated"] is True


def test_a_query_searches_and_says_so(client, three_nodes):
    graph = _graph(client, q="body of mid")

    assert graph["source"] == "search"
    assert [n["id"] for n in graph["nodes"]] == [three_nodes["mid"]]
    assert graph["relations"] == []


def test_node_type_filters_the_window(client, memory, three_nodes):
    memory.append_learning(
        ticket_id="t", workspace_slug=WS, content="Learned a thing.", title="A learning"
    )
    graph = _graph(client, node_type="learning")

    assert [n["title"] for n in graph["nodes"]] == ["A learning"]
    assert graph["type_counts"] == {"memory": 3, "learning": 1}


def test_no_configured_graph_is_a_setup_state_not_an_empty_list(client):
    with patch("loregarden.services.memory_store.resolved_memory_sqlite_path", return_value=None):
        graph = _graph(client)

    assert (graph["configured"], graph["nodes"]) == (False, [])


def test_a_configured_but_unwritten_graph_is_empty_and_is_not_created(client, memory):
    path = memory.graph_path("never-written")
    graph = client.get("/api/memory/graph", params={"workspace_slug": "never-written"}).json()

    assert (graph["configured"], graph["nodes"]) == (True, [])
    assert path is not None and not path.exists()


def test_a_discredited_node_is_out_of_the_graph_but_readable_by_id(client, memory, three_nodes):
    memory.set_discredited(
        node_id=three_nodes["mid"], workspace_slug=WS, discredited=True, reason="r", writer="t"
    )

    assert three_nodes["mid"] not in {n["id"] for n in _graph(client)["nodes"]}
    record = client.get(f"/api/memory/nodes/{three_nodes['mid']}", params={"workspace_slug": WS})
    assert record.status_code == 200
    assert record.json()["discredited"] is True


def test_an_unknown_node_id_is_404(client, three_nodes):
    response = client.get("/api/memory/nodes/nope", params={"workspace_slug": WS})
    assert response.status_code == 404


def test_the_node_record_carries_neighbours_both_ways(client, three_nodes):
    record = client.get(
        f"/api/memory/nodes/{three_nodes['new']}", params={"workspace_slug": WS}
    ).json()

    edges = {(r["direction"], r["node_id"], r["relation_type"]) for r in record["relations"]}
    assert edges == {
        ("out", three_nodes["old"], "related"),
        ("out", three_nodes["mid"], "supports"),
    }
    assert record["ladder"] == {
        "clean_pass": 0,
        "passed_after_autofix": 0,
        "rerouted": 0,
        "blocked": 0,
    }


def test_the_discredit_response_is_the_full_record(client, three_nodes):
    """It returned the node without `relations`; the /memory page then crashed reading them."""
    response = client.put(
        f"/api/memory/nodes/{three_nodes['new']}/discredited",
        json={"workspace_slug": WS, "discredited": True, "reason": "wrong"},
    ).json()

    assert len(response["relations"]) == 2
    assert response["superseded_by"] == []


# ---------------------------------------------------------------------------
# Provenance
# ---------------------------------------------------------------------------


def test_a_node_with_no_recorded_origin_reads_back_as_null(client, three_nodes):
    record = client.get(
        f"/api/memory/nodes/{three_nodes['old']}", params={"workspace_slug": WS}
    ).json()
    assert (record["origin_kind"], record["origin_ref"]) == (None, None)
    node = next(n for n in _graph(client)["nodes"] if n["id"] == three_nodes["old"])
    assert (node["origin_kind"], node["origin_ref"]) == (None, None)


def test_origin_is_kept_by_later_writes_that_do_not_state_one(memory):
    graph = memory.require_graph(WS)
    node = graph.upsert_node(
        title="T",
        body="b",
        workspace_slug=WS,
        origin_kind=MemoryOriginKind.IMPORT,
        origin_ref="vault/old.md",
    )
    graph.upsert_node(node_id=node["id"], title="T2", body="b2", workspace_slug=WS)

    stored = graph.get_node(node["id"])
    assert (stored["origin_kind"], stored["origin_ref"]) == ("import", "vault/old.md")


def _mcp_append(client, *, orchestrated: bool) -> dict:
    headers = {"X-Loregarden-Orchestrated": "1"} if orchestrated else {}
    response = client.post(
        "/mcp",
        headers=headers,
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {
                "name": "loregarden_append_learning",
                "arguments": {"ticket_id": "t", "workspace_slug": WS, "content": "A lesson."},
            },
        },
    )
    result = response.json()["result"]
    assert not result.get("isError"), result
    return json.loads(result["content"][0]["text"])["graph"]


def test_an_orchestrated_agent_write_is_recorded_as_agent(client):
    node = _mcp_append(client, orchestrated=True)
    # No run header was sent, so the reference stays unknown.
    assert (node["origin_kind"], node["origin_ref"]) == ("agent", None)


def test_a_write_from_an_unidentified_caller_has_no_origin(client):
    node = _mcp_append(client, orchestrated=False)
    assert (node["origin_kind"], node["origin_ref"]) == (None, None)


def test_discredited_nodes_are_in_the_graph_only_when_asked_for(client, memory, three_nodes):
    memory.set_discredited(
        node_id=three_nodes["mid"], workspace_slug=WS, discredited=True, reason="r", writer="t"
    )

    included = _graph(client, include_discredited=True)
    searched = _graph(client, include_discredited=True, q="body of mid")

    mid = next(n for n in included["nodes"] if n["id"] == three_nodes["mid"])
    assert mid["discredited"] is True
    assert included["include_discredited"] is True
    assert included["counts"]["links"] == 2
    assert [n["id"] for n in searched["nodes"]] == [three_nodes["mid"]]
