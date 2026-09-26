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
