"""Cutover ACs for lg-improved-memory-662 — GRAPH record, vault export.

Pins R1–R8 of the memory-store authority cutover. These tests define the contract
the implementer must green; peer dual-write / both-stores recall are false.

R9 (rewrite peer-dual-write tests) lives alongside in test_memory_store.py and
test_inherited_wisdom_result.py — those files must not keep the old assertions.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from loregarden.models.domain import MemoryStoreKind, MemoryStoreState
from loregarden.services.memory_store import (
    AgentMemoryService,
    ObsidianMemoryStore,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def vault_dir(tmp_path):
    vault = tmp_path / "vault"
    vault.mkdir()
    return vault


def _both(vault_dir: Path, tmp_path: Path) -> AgentMemoryService:
    return AgentMemoryService(
        obsidian=ObsidianMemoryStore(vault_dir),
        graph_sqlite_base=tmp_path / "Loregarden" / "memory.db",
    )


def _graph_only(tmp_path: Path) -> AgentMemoryService:
    return AgentMemoryService(
        obsidian=None,
        graph_sqlite_base=tmp_path / "Loregarden" / "memory.db",
    )


def _vault_only(vault_dir: Path) -> AgentMemoryService:
    return AgentMemoryService(obsidian=ObsidianMemoryStore(vault_dir), graph_sqlite_base=None)


def _frontmatter(path: Path) -> dict[str, object]:
    """Parse simple YAML-ish frontmatter produced by `_format_frontmatter`."""
    text = path.read_text(encoding="utf-8")
    assert text.startswith("---\n"), path
    end = text.find("\n---\n", 4)
    assert end > 0, path
    block = text[4:end]
    out: dict[str, object] = {}
    for line in block.splitlines():
        if ":" not in line or line.startswith("  -"):
            continue
        key, raw = line.split(":", 1)
        value = raw.strip()
        if value in ("true", "false"):
            out[key] = value == "true"
        elif value.startswith('"') and value.endswith('"'):
            out[key] = value[1:-1].replace('\\"', '"')
        else:
            out[key] = value
    return out


# ---------------------------------------------------------------------------
# R1 — authority map single-sourced in memory_authority
# ---------------------------------------------------------------------------


def test_r1_authority_map_names_graph_for_memory_learning_relations():
    """Cutover R1 — durable knowledge kinds record in GRAPH."""
    from loregarden.services import memory_authority

    assert memory_authority.authoritative_store("memory") == MemoryStoreKind.GRAPH
    assert memory_authority.authoritative_store("learning") == MemoryStoreKind.GRAPH
    assert memory_authority.authoritative_store("relation") == MemoryStoreKind.GRAPH


def test_r1_authority_map_names_vault_for_blog_and_checkpoint():
    """Cutover R1 — blog_post and checkpoint remain vault-native records."""
    from loregarden.services import memory_authority

    assert memory_authority.authoritative_store("blog_post") == MemoryStoreKind.VAULT
    assert memory_authority.authoritative_store("checkpoint") == MemoryStoreKind.VAULT


def test_r1_memory_and_learning_vault_role_is_export_not_record():
    """Cutover R1 — vault is labelled export for memory/learning, not a peer record."""
    from loregarden.services import memory_authority

    assert memory_authority.vault_role("memory") == "export"
    assert memory_authority.vault_role("learning") == "export"
    assert memory_authority.vault_role("blog_post") == "record"
    assert memory_authority.vault_role("checkpoint") == "record"


# ---------------------------------------------------------------------------
# R2 — graph-then-export, shared node_id, fail closed without graph
# ---------------------------------------------------------------------------


def test_r2_append_learning_shares_one_node_id_across_graph_and_export(vault_dir, tmp_path):
    """Cutover R2 — response obsidian.id == graph.id when vault is configured."""
    service = _both(vault_dir, tmp_path)
    result = service.append_learning(
        ticket_id="t-shared",
        workspace_slug="lg",
        content="Shared node_id learning body for cutover.",
    )
    assert "graph" in result and "obsidian" in result
    assert result["obsidian"]["id"] == result["graph"]["id"]
    assert result["graph"]["id"]


def test_r2_upsert_memory_shares_one_node_id_across_graph_and_export(vault_dir, tmp_path):
    """Cutover R2 — upsert_memory also shares a single node_id."""
    service = _both(vault_dir, tmp_path)
    result = service.upsert_memory(
        title="Shared id memory",
        body="Graph first then labelled export.",
        workspace_slug="lg",
    )
    assert result["obsidian"]["id"] == result["graph"]["id"]


def test_r2_append_learning_writes_graph_before_vault_export(vault_dir, tmp_path):
    """Cutover R2 — GRAPH is the record: a vault write that runs first and mints
    its own uuid is the dual-uuid bug. Graph node must exist under the shared id
    even if we inspect the DB before trusting the export path."""
    service = _both(vault_dir, tmp_path)
    result = service.append_learning(
        ticket_id="t-order",
        workspace_slug="lg",
        content="Ordering proof for graph-then-export.",
    )
    node_id = result["graph"]["id"]
    graph = service._graph_for_workspace("lg")
    rows = [r for r in graph.list_nodes(workspace_slug="lg") if r["id"] == node_id]
    assert len(rows) == 1
    assert rows[0]["node_type"] == "learning"


def test_r2_memory_learning_writes_fail_closed_without_graph(vault_dir):
    """Cutover R2 — vault-only config must not create memory/learning records."""
    service = _vault_only(vault_dir)
    with pytest.raises((ValueError, RuntimeError)):
        service.append_learning(
            ticket_id="t-no-graph",
            workspace_slug="lg",
            content="Must not land as vault-only peer write.",
        )
    with pytest.raises((ValueError, RuntimeError)):
        service.upsert_memory(
            title="Vault only memory",
            body="Must raise.",
            workspace_slug="lg",
        )


def test_r2_graph_only_learning_write_succeeds_without_vault(tmp_path):
    """Cutover R2 edge — graph-only is a valid deployment; export is optional."""
    service = _graph_only(tmp_path)
    result = service.append_learning(
        ticket_id="t-graph-only",
        workspace_slug="lg",
        content="No vault configured; graph is enough.",
    )
    assert "graph" in result
    assert "obsidian" not in result
    assert result["graph"]["id"]


def test_r2_blog_and_checkpoint_remain_vault_native_without_graph(vault_dir):
    """Cutover R2 — fail-closed applies to memory/learning only."""
    service = _vault_only(vault_dir)
    blog = service.upsert_blog_post(
        ticket_id="t-blog",
        workspace_slug="lg",
        title="Retrospective",
        body="Vault-native blog stays.",
    )
    assert "obsidian" in blog and "graph" not in blog
    cp = service.append_checkpoint(
        ticket_id="t-cp",
        workspace_slug="lg",
        run_id="run-1",
        entry="Assumption kept in vault.",
    )
    assert "obsidian" in cp and "graph" not in cp


# ---------------------------------------------------------------------------
# R3 — export frontmatter: graph id + derived: true
# ---------------------------------------------------------------------------


def test_r3_exported_learning_frontmatter_has_graph_id_and_derived_true(vault_dir, tmp_path):
    """Cutover R3 — labelled export so hand-edits are not mistaken for the record."""
    service = _both(vault_dir, tmp_path)
    result = service.append_learning(
        ticket_id="t-derived",
        workspace_slug="lg",
        content="Export must carry derived:true.",
    )
    path = vault_dir / result["obsidian"]["path"]
    meta = _frontmatter(path)
    assert meta.get("id") == result["graph"]["id"]
    assert meta.get("derived") is True


def test_r3_exported_memory_frontmatter_has_graph_id_and_derived_true(vault_dir, tmp_path):
    """Cutover R3 — same labelling for durable memory exports."""
    service = _both(vault_dir, tmp_path)
    result = service.upsert_memory(
        title="Derived memory note",
        body="Frontmatter id matches graph.",
        workspace_slug="lg",
    )
    path = vault_dir / result["obsidian"]["path"]
    meta = _frontmatter(path)
    assert meta.get("id") == result["graph"]["id"]
    assert meta.get("derived") is True


# ---------------------------------------------------------------------------
# R4 — rebuild_workspace_exports + orphan quarantine
# ---------------------------------------------------------------------------


def test_r4_rebuild_projects_every_graph_memory_and_learning(vault_dir, tmp_path):
    """Cutover R4 — one-shot rebuild writes vault files with matching ids."""
    from loregarden.services.memory_export import rebuild_workspace_exports

    service = _both(vault_dir, tmp_path)
    graph = service._graph_for_workspace("lg")
    mem = graph.upsert_node(
        title="Graph memory A",
        body="Body A.",
        workspace_slug="lg",
        node_type="memory",
    )
    learn = graph.upsert_node(
        title="Learning — t-rebuild",
        body="Body L.",
        workspace_slug="lg",
        node_type="learning",
        ticket_id="t-rebuild",
    )

    summary = rebuild_workspace_exports(service, workspace_slug="lg")
    assert summary["exported"] >= 2

    mem_hits = list((vault_dir / "Loregarden" / "Memory" / "lg").rglob("*.md"))
    learn_hits = list((vault_dir / "Loregarden" / "Learnings" / "lg").rglob("*.md"))
    mem_ids = {_frontmatter(p).get("id") for p in mem_hits if "_orphans" not in p.parts}
    learn_ids = {_frontmatter(p).get("id") for p in learn_hits if "_orphans" not in p.parts}
    assert mem["id"] in mem_ids
    assert learn["id"] in learn_ids
    assert all(
        _frontmatter(p).get("derived") is True
        for p in mem_hits + learn_hits
        if "_orphans" not in p.parts
    )


def test_r4_rebuild_quarantines_vault_files_whose_id_is_absent_from_graph(vault_dir, tmp_path):
    """Cutover R4 — orphans move under {memory|learnings}/_orphans/{workspace}/."""
    from loregarden.services.memory_export import rebuild_workspace_exports

    service = _both(vault_dir, tmp_path)
    # Pre-cutover vault ghost with an id the graph does not hold.
    ghost = service.obsidian.upsert_note(
        note_id="orphan-id-not-in-graph",
        title="Orphan vault memory",
        body="Should be quarantined, not deleted.",
        workspace_slug="lg",
        note_type="memory",
    )
    ghost_path = vault_dir / ghost.path
    assert ghost_path.is_file()

    graph = service._graph_for_workspace("lg")
    graph.upsert_node(
        title="Kept graph memory",
        body="Stays as export.",
        workspace_slug="lg",
        node_type="memory",
    )

    rebuild_workspace_exports(service, workspace_slug="lg")

    assert not ghost_path.is_file()
    orphan_root = vault_dir / "Loregarden" / "Memory" / "_orphans" / "lg"
    orphaned = list(orphan_root.rglob("*.md"))
    assert orphaned, "orphan must be moved, never deleted"
    assert any("Orphan vault memory" in p.read_text(encoding="utf-8") for p in orphaned)


def test_r4_orphans_are_not_search_or_recall_inputs(vault_dir, tmp_path):
    """Cutover R4 — quarantined files must not feed search/recall."""
    from loregarden.services.memory_export import rebuild_workspace_exports

    service = _both(vault_dir, tmp_path)
    service.obsidian.upsert_note(
        note_id="orphan-search-needle-id",
        title="Orphan unique needle xyz662",
        body="orphan-search-needle-xyz662 body",
        workspace_slug="lg",
        note_type="memory",
    )
    service._graph_for_workspace("lg").upsert_node(
        title="Live graph node",
        body="unrelated",
        workspace_slug="lg",
        node_type="memory",
    )
    rebuild_workspace_exports(service, workspace_slug="lg")

    found = service.search("orphan-search-needle-xyz662", workspace_slug="lg")
    assert found["obsidian"] == []
    assert found["graph"] == []
    assert service.recall_related("orphan-search-needle-xyz662", workspace_slug="lg") == []


# ---------------------------------------------------------------------------
# R5 — recall_related is GRAPH-only for durable knowledge
# ---------------------------------------------------------------------------


def test_r5_recall_related_returns_only_sqlite_source_candidates(vault_dir, tmp_path):
    """Cutover R5 — every durable hit carries source=sqlite."""
    service = _both(vault_dir, tmp_path)
    service._graph_for_workspace("lg").upsert_node(
        title="Retry budget",
        body="A throttled server returns before the trusted retry loop runs.",
        workspace_slug="lg",
        node_type="memory",
    )
    service.obsidian.upsert_note(
        title="Trusted server throttle",
        body="Cap the call rate per tool.",
        workspace_slug="lg",
        note_type="memory",
    )

    ranked = service.recall_related("trusted server", workspace_slug="lg")
    assert ranked, "graph hit must surface"
    assert all(row["source"] == "sqlite" for row in ranked)
    assert {row["title"] for row in ranked} == {"Retry budget"}


def test_r5_recall_related_never_returns_blog_posts(vault_dir, tmp_path):
    """Cutover R5 — blog_post is not agent memory."""
    service = _both(vault_dir, tmp_path)
    service.upsert_blog_post(
        ticket_id="t-blog",
        workspace_slug="lg",
        title="Trusted server retrospective",
        body="A trusted server story that must not enter recall.",
    )
    service._graph_for_workspace("lg").upsert_node(
        title="Retry budget",
        body="trusted server throttle policy",
        workspace_slug="lg",
        node_type="memory",
    )

    ranked = service.recall_related("trusted server", workspace_slug="lg")
    assert [row["title"] for row in ranked] == ["Retry budget"]
    assert all(row.get("note_type") != "blog_post" for row in ranked)


def test_r5_vault_hand_edit_cannot_change_recall_ranking(vault_dir, tmp_path):
    """Cutover R5 — hand-edits in Obsidian are not a second record for recall."""
    service = _both(vault_dir, tmp_path)
    written = service.upsert_memory(
        title="Graph rank anchor",
        body="trusted server throttle",
        workspace_slug="lg",
    )
    export_path = vault_dir / written["obsidian"]["path"]
    # Hand-edit: rewrite body to a higher-overlap phrase that would win under
    # the old both-stores ranker if vault were consulted.
    text = export_path.read_text(encoding="utf-8")
    export_path.write_text(
        text.replace(
            "trusted server throttle",
            "trusted server throttle trusted server throttle trusted server",
        ),
        encoding="utf-8",
    )
    # Competing vault-only note with even stronger wording.
    service.obsidian.upsert_note(
        title="Hand edited vault winner",
        body="trusted server throttle trusted server throttle",
        workspace_slug="lg",
        note_type="memory",
    )

    ranked = service.recall_related("trusted server throttle", workspace_slug="lg")
    assert [row["title"] for row in ranked] == ["Graph rank anchor"]
    assert ranked[0]["id"] == written["graph"]["id"]
    assert ranked[0]["source"] == "sqlite"


def test_r5_recall_does_not_open_obsidian_list_notes(vault_dir, tmp_path):
    """Cutover R5 — vault enumeration is off the recall path (cost + authority)."""
    from unittest.mock import patch

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
        service.recall_related("trusted server", workspace_slug="lg")
    assert list_notes.call_count == 0


# ---------------------------------------------------------------------------
# R6 — search() envelope retained; membership filtered by kind
# ---------------------------------------------------------------------------


def test_r6_search_keeps_envelope_keys(vault_dir, tmp_path):
    """Cutover R6 — MCP/client keep {query, workspace_slug, obsidian, graph}."""
    service = _both(vault_dir, tmp_path)
    found = service.search("anything", workspace_slug="lg")
    assert set(found) == {"query", "workspace_slug", "obsidian", "graph"}


def test_r6_search_graph_hits_are_only_memory_or_learning(vault_dir, tmp_path):
    """Cutover R6 — graph array membership is memory|learning only."""
    service = _both(vault_dir, tmp_path)
    service.upsert_memory(title="Mem needle", body="graph-kind-filter-needle", workspace_slug="lg")
    service.append_learning(
        ticket_id="t-kind",
        workspace_slug="lg",
        content="graph-kind-filter-needle learning",
    )
    found = service.search("graph-kind-filter-needle", workspace_slug="lg")
    assert found["graph"]
    assert all(row.get("node_type") in {"memory", "learning"} for row in found["graph"])


def test_r6_search_obsidian_hits_are_only_blog_or_checkpoint(vault_dir, tmp_path):
    """Cutover R6 — vault memory/learning peers must not appear in obsidian[]."""
    service = _both(vault_dir, tmp_path)
    needle = "kind-filter-obsidian-needle-662"
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


# ---------------------------------------------------------------------------
# R7 — protocol embed states record+export (no dual-write peer language)
# ---------------------------------------------------------------------------


def test_r7_protocol_authority_appears_in_first_8000_chars():
    """Cutover R7 — authority text must survive the 8000-char executor embed.

    Pins presence of record/export concepts and absence of peer dual-write map
    language — not unpinned prose.
    """
    # Resolve the same tree the executor embeds from this checkout.
    root = Path(__file__).resolve().parents[2]
    protocol = root / "agent_context" / "agents" / "common_assets" / "memory_protocol_v1.md"
    text = protocol.read_text(encoding="utf-8")
    head = text[:8000].casefold()
    assert "graph" in head
    assert "export" in head or "one-way" in head or "derived" in head
    assert "record" in head
    # Peer dual-write framing that treated vault and graph as equals.
    assert "## dual-write map" not in head
    assert "dual-write" not in head or "export" in head


# ---------------------------------------------------------------------------
# R8 — inherited_wisdom durable-recall telemetry is GRAPH
# ---------------------------------------------------------------------------


def test_r8_durable_briefing_credits_graph_not_vault_for_memory_hits(tmp_path):
    """Cutover R8 — durable memory/learning briefing store is GRAPH."""
    from loregarden.agents.inherited_wisdom import build_inherited_wisdom
    from tests.memory_helpers import briefing_ticket

    vault = tmp_path / "vault"
    vault.mkdir()
    service = _both(vault, tmp_path)
    service.upsert_memory(
        title="Retry budget for throttled tools",
        body=(
            "A throttled server returns early, so the trusted retry loop is called "
            "again before the fast path clears."
        ),
        workspace_slug="lg",
    )
    # Vault-only competing note that would have been a peer hit before cutover.
    service.obsidian.upsert_note(
        title="Vault only trusted server",
        body="trusted MCP server throttle",
        workspace_slug="lg",
        note_type="memory",
    )

    ticket = briefing_ticket(title="Cap how fast a trusted MCP server can be called")
    result = build_inherited_wisdom(ticket, "lg", memory=service)

    assert result.learnings_injected >= 1
    assert result.store_states[MemoryStoreKind.GRAPH] == MemoryStoreState.READ
    # Durable recall must not treat VAULT as a consulted memory store. CHECKPOINTS
    # may still read the vault; the recall set itself is GRAPH-only.
    from loregarden.agents import inherited_wisdom as iw

    assert MemoryStoreKind.GRAPH in iw._RECALL_STORES
    assert MemoryStoreKind.VAULT not in iw._RECALL_STORES


def test_r8_memory_sqlite_path_is_not_control_plane_db(tmp_path, monkeypatch):
    """Cutover R8 — nodes stay in iCloud memory_sqlite_path, not data/loregarden.db."""
    vault = tmp_path / "vault"
    vault.mkdir()
    control_db = tmp_path / "repo" / "data" / "loregarden.db"
    control_db.parent.mkdir(parents=True)
    control_db.write_text("", encoding="utf-8")
    memory_db = tmp_path / "icloud" / "Loregarden" / "memory.db"
    memory_db.parent.mkdir(parents=True)

    monkeypatch.setattr("loregarden.config.settings.obsidian_vault_dir", str(vault))
    monkeypatch.setattr(
        "loregarden.config.settings.memory_sqlite_url",
        f"sqlite:///{memory_db}",
    )
    monkeypatch.setattr(
        "loregarden.config.settings.database_url",
        f"sqlite:///{control_db}",
    )

    service = AgentMemoryService.from_settings()
    status = service.status(workspace_slug="lg")
    assert status["memory_sqlite_path"]
    assert "loregarden.db" not in status["memory_sqlite_path"]
    assert Path(status["memory_sqlite_path"]).resolve() != control_db.resolve()
