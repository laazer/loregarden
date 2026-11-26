"""Deletion contract: vault list_notes must not feed durable recall candidates.

After Cutover R5 (`recall_related` is GRAPH-only), `AgentMemoryService` must not
keep a vault list_notes-backed candidate helper. These tests stay red until that
helper and residual name hits are removed on a post-cutover tree.

The forbidden helper name is assembled at runtime so this file never reintroduces
a contiguous production-token `git grep` hit under `server/`.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from loregarden.models.domain import MemoryStoreKind
from loregarden.services.memory_store import (
    AgentMemoryService,
    MemoryGraphStore,
    MemoryStoreReadError,
    ObsidianMemoryStore,
)


@pytest.fixture
def vault_dir(tmp_path: Path) -> Path:
    vault = tmp_path / "vault"
    vault.mkdir()
    return vault


def _both(vault_dir: Path, tmp_path: Path) -> AgentMemoryService:
    return AgentMemoryService(
        obsidian=ObsidianMemoryStore(vault_dir),
        graph_sqlite_base=tmp_path / "Loregarden" / "memory.db",
    )


# ---------------------------------------------------------------------------
# AC-DELETE / AC-NO-RECALL-HELPER — symbol and residual-mention scrub
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# AC-RECALL-GRAPH-ONLY / AC-R5-GREEN — behavioural recall contract
# ---------------------------------------------------------------------------


def test_recall_related_does_not_enumerate_obsidian(vault_dir, tmp_path):
    """AC-R5-GREEN — vault list_notes stays off the durable recall path."""
    service = _both(vault_dir, tmp_path)
    service.graph_for_workspace("lg").upsert_node(
        title="Trusted server throttle",
        body="Cap the rate.",
        workspace_slug="lg",
        node_type="memory",
    )
    with patch.object(
        service.obsidian, "list_notes", wraps=service.obsidian.list_notes
    ) as list_notes:
        service.recall_related("trusted server", workspace_slug="lg")
    assert list_notes.call_count == 0


def test_recall_related_returns_only_sqlite_sources_when_vault_peer_exists(vault_dir, tmp_path):
    """AC-RECALL-GRAPH-ONLY — vault memory peers must not re-enter recall ranking."""
    service = _both(vault_dir, tmp_path)
    service.obsidian.upsert_note(
        title="Trusted server throttle",
        body="Cap the call rate per tool.",
        workspace_slug="lg",
        note_type="memory",
    )
    service.graph_for_workspace("lg").upsert_node(
        title="Retry budget",
        body="A throttled server returns before the trusted retry loop runs.",
        workspace_slug="lg",
        node_type="memory",
    )

    ranked = service.recall_related("trusted server", workspace_slug="lg")
    assert ranked, "graph hit must surface"
    assert all(row["source"] == "sqlite" for row in ranked)
    assert {row["title"] for row in ranked} == {"Retry budget"}


def test_recall_related_labels_graph_read_failures(vault_dir, tmp_path):
    """AC-RECALL-GRAPH-ONLY — GRAPH MemoryStoreReadError wrapping stays."""
    service = _both(vault_dir, tmp_path)
    service.graph_for_workspace("lg").upsert_node(
        title="Retry budget",
        body="trusted server throttle",
        workspace_slug="lg",
        node_type="memory",
    )
    with patch.object(MemoryGraphStore, "list_nodes", side_effect=OSError("graph unavailable")):
        with pytest.raises(MemoryStoreReadError) as caught:
            service.recall_related("trusted server", workspace_slug="lg")
    assert caught.value.store == MemoryStoreKind.GRAPH
    assert type(caught.value.__cause__) is OSError


def test_recall_related_ignores_failing_vault_list_notes(vault_dir, tmp_path):
    """AC-RECALL-GRAPH-ONLY — vault list_notes failure must not raise VAULT error."""
    service = _both(vault_dir, tmp_path)
    service.graph_for_workspace("lg").upsert_node(
        title="Retry budget",
        body="trusted server throttle",
        workspace_slug="lg",
        node_type="memory",
    )
    with patch.object(ObsidianMemoryStore, "list_notes", side_effect=OSError("vault unavailable")):
        ranked = service.recall_related("trusted server", workspace_slug="lg")
    assert ranked
    assert all(row["source"] == "sqlite" for row in ranked)


def test_empty_query_opens_neither_store(vault_dir, tmp_path):
    """Edge — all-stopword / empty term set must stay zero-IO."""
    service = _both(vault_dir, tmp_path)
    with (
        patch.object(service.obsidian, "list_notes") as list_notes,
        patch.object(MemoryGraphStore, "list_nodes") as list_nodes,
    ):
        assert service.recall_related("the and of", workspace_slug="lg") == []
    assert list_notes.call_count == 0
    assert list_nodes.call_count == 0


# ---------------------------------------------------------------------------
# AC-R6-GREEN — search kind-filter intent (envelope + membership)
# ---------------------------------------------------------------------------


def test_r6_search_obsidian_hits_are_only_blog_or_checkpoint(vault_dir, tmp_path):
    """AC-R6-GREEN — vault memory/learning peers must not appear in obsidian[]."""
    service = _both(vault_dir, tmp_path)
    needle = "kind-filter-vault-needle-804"
    service.upsert_memory(title="Mem export", body=needle, workspace_slug="lg")
    service.append_learning(ticket_id="t-obs", workspace_slug="lg", content=needle)
    service.upsert_blog_post(
        ticket_id="t-blog",
        workspace_slug="lg",
        title="Blog hit",
        body=needle,
    )
    service.append_checkpoint(
        ticket_id="t-cp",
        workspace_slug="lg",
        run_id="run-kind",
        entry=f"### Assumption\n{needle}",
    )

    found = service.search(needle, workspace_slug="lg")
    assert {row.get("note_type") for row in found["obsidian"]} <= {"blog_post", "checkpoint"}
    assert all(row.get("note_type") != "memory" for row in found["obsidian"])
    assert all(row.get("note_type") != "learning" for row in found["obsidian"])
    assert {row.get("node_type") for row in found["graph"]} <= {"memory", "learning"}
