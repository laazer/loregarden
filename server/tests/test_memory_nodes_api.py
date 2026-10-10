"""Operator endpoints over graph memory: list, detail, discredit / restore."""

from __future__ import annotations

import pytest
from loregarden.services.memory_store import AgentMemoryService


@pytest.fixture
def node_id() -> str:
    service = AgentMemoryService.from_settings()
    return service.upsert_memory(title="Lesson", body="Body", workspace_slug="ws")["graph"]["id"]


def _discredit(client, node_id, *, discredited=True, reason="Wrong about journals"):
    return client.put(
        f"/api/memory/nodes/{node_id}/discredited",
        json={"workspace_slug": "ws", "discredited": discredited, "reason": reason},
    )


def test_discredit_hides_from_the_default_listing_and_shows_when_asked(client, node_id):
    assert _discredit(client, node_id).status_code == 200

    default = client.get("/api/memory/nodes", params={"workspace_slug": "ws"}).json()
    explicit = client.get(
        "/api/memory/nodes", params={"workspace_slug": "ws", "include_discredited": True}
    ).json()

    assert default["nodes"] == []
    assert [n["id"] for n in explicit["nodes"]] == [node_id]
    assert explicit["nodes"][0]["discredited"] is True


def test_discredit_stores_the_reason_and_restore_reverses_it(client, node_id):
    marked = _discredit(client, node_id).json()
    assert marked["discredited"] is True
    assert marked["versions"][-1]["change_note"] == "Wrong about journals"
    assert marked["versions"][-1]["superseded_by"] == "operator"

    restored = _discredit(client, node_id, discredited=False, reason="Verified again").json()
    assert restored["discredited"] is False
    assert restored["versions"][-1]["change_note"] == "Verified again"


def test_detail_carries_confidence_and_the_ladder(client, node_id):
    detail = client.get(f"/api/memory/nodes/{node_id}", params={"workspace_slug": "ws"}).json()

    assert detail["confidence"]["observations"] == 0
    assert set(detail["ladder"]) == {"clean_pass", "passed_after_autofix", "rerouted", "blocked"}


def test_missing_node_is_404(client):
    assert _discredit(client, "nope").status_code == 404
    assert client.get("/api/memory/nodes/nope", params={"workspace_slug": "ws"}).status_code == 404


def test_blank_reason_is_rejected(client, node_id):
    assert _discredit(client, node_id, reason="   ").status_code == 422


def _second(title: str) -> str:
    return AgentMemoryService.from_settings().upsert_memory(
        title=title, body="other", workspace_slug="ws"
    )["graph"]["id"]


def test_graph_health_records_a_snapshot_and_compares_with_it(client, node_id):
    first = client.post("/api/memory/graph-health/snapshots", json={"workspace_slug": "ws"})
    assert first.status_code == 200 and first.json()["previous"] is None

    report = client.get("/api/memory/graph-health", params={"workspace_slug": "ws"}).json()
    assert report["previous"]["figures"]["learnings"] == 1
    assert report["current"]["shares"]["unlinked"] == 100.0
    history = client.get("/api/memory/graph-health/snapshots", params={"workspace_slug": "ws"})
    assert len(history.json()) == 1


def test_proposals_retitle_and_merge_over_http(client, node_id):
    other = _second("Lesson copy")
    assert client.get("/api/memory/proposals", params={"workspace_slug": "ws"}).status_code == 200

    renamed = client.put(
        f"/api/memory/nodes/{node_id}/title",
        json={"workspace_slug": "ws", "title": "Real name", "reason": "rename"},
    ).json()
    assert (renamed["title"], renamed["aliases"]) == ("Real name", ["Lesson"])

    merged = client.post(
        f"/api/memory/nodes/{node_id}/merge",
        json={"workspace_slug": "ws", "absorbed_id": other, "reason": "same"},
    )
    assert merged.status_code == 200
    lineage = client.get(f"/api/memory/nodes/{node_id}/lineage", params={"workspace_slug": "ws"})
    assert [s["id"] for s in lineage.json()["steps"]] == [other, node_id]


def test_merge_into_itself_is_a_conflict(client, node_id):
    response = client.post(
        f"/api/memory/nodes/{node_id}/merge",
        json={"workspace_slug": "ws", "absorbed_id": node_id, "reason": "r"},
    )
    assert response.status_code == 409


def test_an_operator_can_mark_one_side_superseded(client, node_id):
    other = _second("Newer lesson")
    made = client.post(
        "/api/memory/relations",
        json={
            "workspace_slug": "ws",
            "source_id": other,
            "target_id": node_id,
            "relation_type": "supersedes",
        },
    )
    assert made.status_code == 200 and made.json()["created"] is True
    detail = client.get(f"/api/memory/nodes/{node_id}", params={"workspace_slug": "ws"}).json()
    assert detail["superseded_by"] == [{"id": other, "title": "Newer lesson"}]
    bad = client.post(
        "/api/memory/relations",
        json={
            "workspace_slug": "ws",
            "source_id": node_id,
            "target_id": node_id,
            "relation_type": "related",
        },
    )
    assert bad.status_code == 422
