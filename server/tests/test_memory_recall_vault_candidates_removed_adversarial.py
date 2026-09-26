"""Adversarial / mutation fortification for vault-recall-candidate deletion.

Complements ``test_memory_recall_vault_candidates_removed.py`` (happy-path AC
pins). These cases target seams a rename-shim, leftover VAULT error wrapper,
or search-routed recall would still fail after the dead helper is deleted.

The forbidden helper name is assembled at runtime so this file never
reintroduces a contiguous production-token ``git grep`` hit under ``server/``.
"""

from __future__ import annotations

import inspect
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

_DEAD_HELPER = "_" + "obsidian_candidates"
_ALT_HELPER = "_" + "vault_candidates"


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
# Source / shape mutations — rename, leave VAULT wrapper, route via search
# ---------------------------------------------------------------------------


def test_recall_related_source_has_no_vault_read_error_wrapping():
    """Mutation — deleting the helper but leaving try/except VAULT wrapping
    keeps vault failures on the durable recall path. Post-deletion source must
    only wrap GRAPH reads."""
    source = inspect.getsource(AgentMemoryService.recall_related)
    assert "MemoryStoreKind.GRAPH" in source
    assert "MemoryStoreKind.VAULT" not in source
    assert "list_notes" not in source
    assert _DEAD_HELPER not in source
    assert _ALT_HELPER not in source


def test_recall_related_does_not_delegate_to_search(vault_dir, tmp_path):
    """Assumption check — a search()-first recall reopens vault kind walks and
    defeats GRAPH-only durable ranking."""
    service = _both(vault_dir, tmp_path)
    service._graph_for_workspace("lg").upsert_node(
        title="Retry budget",
        body="trusted server throttle",
        workspace_slug="lg",
        node_type="memory",
    )
    with patch.object(AgentMemoryService, "search", wraps=service.search) as search:
        ranked = service.recall_related("trusted server", workspace_slug="lg")
    assert ranked
    assert search.call_count == 0


def test_no_service_method_named_like_vault_recall_candidate_builder():
    """Rename mutation — `_vault_candidates` / dead-helper aliases must not
    remain as callable attributes after the scrub."""
    for name in (_DEAD_HELPER, _ALT_HELPER, "obsidian_candidates", "vault_candidates"):
        assert not hasattr(AgentMemoryService, name), name


# ---------------------------------------------------------------------------
# Boundary / stress / null-backend
# ---------------------------------------------------------------------------


def test_limit_zero_returns_empty_without_opening_vault(vault_dir, tmp_path):
    """Boundary — limit=0 must short-circuit after ranking without vault IO."""
    service = _both(vault_dir, tmp_path)
    service.obsidian.upsert_note(
        title="Trusted server vault peer",
        body="Cap the rate hard.",
        workspace_slug="lg",
        note_type="memory",
    )
    service._graph_for_workspace("lg").upsert_node(
        title="Trusted server graph",
        body="Cap the rate in graph.",
        workspace_slug="lg",
        node_type="memory",
    )
    with patch.object(
        service.obsidian, "list_notes", wraps=service.obsidian.list_notes
    ) as list_notes:
        ranked = service.recall_related("trusted server", workspace_slug="lg", limit=0)
    assert ranked == []
    assert list_notes.call_count == 0


def test_repeated_recall_never_opens_list_notes(vault_dir, tmp_path):
    """Stress — N recalls must not accumulate vault enumeration."""
    service = _both(vault_dir, tmp_path)
    service._graph_for_workspace("lg").upsert_node(
        title="Trusted server",
        body="Cap the rate.",
        workspace_slug="lg",
        node_type="memory",
    )
    with patch.object(
        service.obsidian, "list_notes", wraps=service.obsidian.list_notes
    ) as list_notes:
        for _ in range(25):
            service.recall_related("trusted server", workspace_slug="lg")
    assert list_notes.call_count == 0


def test_recall_with_obsidian_none_still_ranks_graph(tmp_path):
    """Null vault — deleting the helper must not leave a getattr crash when
    obsidian is unset; graph-only services remain valid."""
    service = AgentMemoryService(
        obsidian=None,
        graph_sqlite_base=tmp_path / "Loregarden" / "memory.db",
    )
    service._graph_for_workspace("lg").upsert_node(
        title="Trusted server",
        body="Cap the rate.",
        workspace_slug="lg",
        node_type="memory",
    )
    ranked = service.recall_related("trusted server", workspace_slug="lg")
    assert ranked
    assert all(row["source"] == "sqlite" for row in ranked)


# ---------------------------------------------------------------------------
# Content mutations — vault peers that would dominate pre-cutover ranking
# ---------------------------------------------------------------------------


def test_high_overlap_vault_memory_cannot_outrank_weaker_graph_hit(vault_dir, tmp_path):
    """Mutation — a vault memory note that shares every query term must not
    re-enter ranking above a weaker graph hit."""
    service = _both(vault_dir, tmp_path)
    service.obsidian.upsert_note(
        title="Trusted server throttle retry budget",
        body="Trusted server throttle retry budget exact match vault peer.",
        workspace_slug="lg",
        note_type="memory",
    )
    service._graph_for_workspace("lg").upsert_node(
        title="Partial",
        body="trusted server somewhere in graph only",
        workspace_slug="lg",
        node_type="memory",
    )
    with patch.object(
        service.obsidian, "list_notes", wraps=service.obsidian.list_notes
    ) as list_notes:
        ranked = service.recall_related("trusted server throttle retry budget", workspace_slug="lg")
    assert list_notes.call_count == 0
    assert ranked
    assert all(row["source"] == "sqlite" for row in ranked)
    assert {row["title"] for row in ranked} == {"Partial"}


def test_vault_learning_and_blog_peers_never_appear_in_recall(vault_dir, tmp_path):
    """Combinatorial — vault learning export + blog post overlap must stay out
    of durable recall even when graph has a weaker hit."""
    service = _both(vault_dir, tmp_path)
    needle = "trusted server throttle combinatorial-804"
    service.obsidian.upsert_note(
        title="Vault learning peer",
        body=needle,
        workspace_slug="lg",
        note_type="learning",
    )
    service.upsert_blog_post(
        ticket_id="t-blog-adv",
        workspace_slug="lg",
        title="Blog peer",
        body=needle,
    )
    service._graph_for_workspace("lg").upsert_node(
        title="Graph weak",
        body="trusted server only",
        workspace_slug="lg",
        node_type="memory",
    )
    with patch.object(
        service.obsidian, "list_notes", wraps=service.obsidian.list_notes
    ) as list_notes:
        ranked = service.recall_related(needle, workspace_slug="lg")
    assert list_notes.call_count == 0
    assert ranked
    assert all(row["source"] == "sqlite" for row in ranked)
    assert {row["title"] for row in ranked} == {"Graph weak"}


def test_graph_failure_still_labels_graph_not_vault(vault_dir, tmp_path):
    """Error handling — GRAPH read failure must remain GRAPH-labelled; a leftover
    VAULT wrapper must not steal the label when list_notes is unused."""
    service = _both(vault_dir, tmp_path)
    service._graph_for_workspace("lg").upsert_node(
        title="Retry budget",
        body="trusted server",
        workspace_slug="lg",
        node_type="memory",
    )
    with patch.object(MemoryGraphStore, "list_nodes", side_effect=OSError("graph down")):
        with patch.object(ObsidianMemoryStore, "list_notes", side_effect=OSError("vault down")):
            with pytest.raises(MemoryStoreReadError) as caught:
                service.recall_related("trusted server", workspace_slug="lg")
    assert caught.value.store == MemoryStoreKind.GRAPH
    assert type(caught.value.__cause__) is OSError
    assert "graph down" in str(caught.value.__cause__)


def test_only_graph_candidates_feeds_recall_source_shape():
    """AC-SCOPE / mutation — recall_related must append candidates only via
    `_graph_candidates`; no second candidate builder call site."""
    source = inspect.getsource(AgentMemoryService.recall_related)
    assert source.count("_graph_candidates") == 1
    # A reintroduced vault path typically shows up as a second `candidates +=`.
    assert source.count("candidates +=") == 1
