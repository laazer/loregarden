"""Adversarial / edge / mutation tests for lg-improved-memory-662 cutover.

Complements ``test_memory_authority_cutover.py`` (happy-path R1–R8). These cases
target seams a thin filter-only or dual-uuid-shim implementation would still
fail: partial write failure, missing-id orphans, cross-workspace bleed,
idempotent rebuild, learning-role protocol copy, and search/recall under
error and empty inputs.

Does not change expected behaviour — only fortifies the red suite the
implementer must green.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from loregarden.models.domain import MemoryStoreKind
from loregarden.services.memory_store import (
    AgentMemoryService,
    ObsidianMemoryStore,
)

# ---------------------------------------------------------------------------
# Helpers (mirrors cutover suite so this file stays runnable alone)
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


def _frontmatter(path: Path) -> dict[str, object]:
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


def _write_raw_vault_note(
    vault: Path,
    *,
    subdir: str,
    workspace: str,
    filename: str,
    frontmatter: dict[str, object],
    body: str,
) -> Path:
    """Plant a vault file without going through AgentMemoryService (hand-edit /
    pre-cutover ghost simulation)."""
    target = vault / "Loregarden" / subdir / workspace / filename
    target.parent.mkdir(parents=True, exist_ok=True)
    lines = ["---"]
    for key, value in frontmatter.items():
        if isinstance(value, bool):
            lines.append(f"{key}: {'true' if value else 'false'}")
        else:
            escaped = str(value).replace('"', '\\"')
            lines.append(f'{key}: "{escaped}"')
    lines.append("---")
    lines.append("")
    lines.append(body)
    target.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return target


# ---------------------------------------------------------------------------
# Null / empty / boundary
# ---------------------------------------------------------------------------


def test_search_empty_and_whitespace_query_keeps_envelope_and_empty_hits(vault_dir, tmp_path):
    """Boundary — empty/whitespace must not crash or invent hits; envelope stays."""
    service = _both(vault_dir, tmp_path)
    for query in ("", "   ", "\t\n"):
        found = service.search(query, workspace_slug="lg")
        assert set(found) == {"query", "workspace_slug", "obsidian", "graph"}
        assert found["obsidian"] == []
        assert found["graph"] == []


def test_search_limit_zero_returns_envelope_with_empty_arrays(vault_dir, tmp_path):
    """Boundary — limit=0 must not bypass kind filters or raise."""
    service = _both(vault_dir, tmp_path)
    service.upsert_memory(title="Cap", body="limit-zero-needle", workspace_slug="lg")
    found = service.search("limit-zero-needle", workspace_slug="lg", limit=0)
    assert set(found) == {"query", "workspace_slug", "obsidian", "graph"}
    assert found["obsidian"] == []
    assert found["graph"] == []


def test_authoritative_store_rejects_unknown_kind():
    """Invalid input — closed vocabulary must not silently default to VAULT."""
    from loregarden.services import memory_authority

    with pytest.raises((ValueError, KeyError)):
        memory_authority.authoritative_store("embedding_cache")


# ---------------------------------------------------------------------------
# R2 — partial failure, identity stability, fail-closed extremes
# ---------------------------------------------------------------------------


def test_r2_export_failure_after_graph_write_leaves_graph_node(vault_dir, tmp_path):
    """Error handling — GRAPH is the record: a vault OSError after graph commit
    must not roll back the node. A shim that writes vault first then graph would
    leave neither or only vault under a different id."""
    service = _both(vault_dir, tmp_path)
    graph = service._graph_for_workspace("lg")
    assert graph is not None

    # Cutover writes GRAPH first, then export. Patch only the vault half so a
    # graph-first implementation leaves the node; a vault-first dual-write never
    # reaches the graph and fails this assertion.
    with patch.object(
        service.obsidian, "append_learning", side_effect=OSError("vault export unavailable")
    ):
        with patch.object(
            service.obsidian, "upsert_note", side_effect=OSError("vault export unavailable")
        ):
            with pytest.raises(OSError, match="vault export"):
                service.append_learning(
                    ticket_id="t-partial",
                    workspace_slug="lg",
                    content="Graph must survive export failure.",
                )

    rows = [
        r
        for r in graph.list_nodes(workspace_slug="lg")
        if "Graph must survive export failure" in (r.get("body") or "")
    ]
    assert len(rows) == 1, "graph-first write must leave the learning node even when export fails"
    assert rows[0]["node_type"] == "learning"


def test_r2_upsert_memory_update_keeps_shared_node_id(vault_dir, tmp_path):
    """Order / state — re-upsert must not mint a second uuid for vault vs graph."""
    service = _both(vault_dir, tmp_path)
    first = service.upsert_memory(
        title="Stable id",
        body="first body",
        workspace_slug="lg",
    )
    node_id = first["graph"]["id"]
    second = service.upsert_memory(
        node_id=node_id,
        title="Stable id",
        body="second body revised",
        workspace_slug="lg",
    )
    assert second["graph"]["id"] == node_id
    assert second["obsidian"]["id"] == node_id
    path = vault_dir / second["obsidian"]["path"]
    assert _frontmatter(path).get("id") == node_id
    assert "second body revised" in path.read_text(encoding="utf-8")


def test_r2_neither_backend_configured_fails_closed(tmp_path, monkeypatch):
    """Null backends — no silent success when both stores are absent.

    ``graph_sqlite_base=None`` alone is not enough: ``_graph_path_for_workspace``
    falls through to ``resolved_memory_sqlite_path()`` from settings, which on a
    developer machine can be a live iCloud DB. Pin both halves off.
    """
    monkeypatch.setattr(
        "loregarden.services.memory_store.resolved_memory_sqlite_path",
        lambda: None,
    )
    service = AgentMemoryService(obsidian=None, graph_sqlite_base=None)
    with pytest.raises((ValueError, RuntimeError)):
        service.append_learning(
            ticket_id="t-none",
            workspace_slug="lg",
            content="nowhere to land",
        )
    with pytest.raises((ValueError, RuntimeError)):
        service.upsert_memory(title="x", body="y", workspace_slug="lg")


# ---------------------------------------------------------------------------
# R3 / R5 — hand-edit mutations cannot re-enter recall or search as peers
# ---------------------------------------------------------------------------


def test_r3_hand_edit_derived_false_still_excluded_from_search_obsidian(vault_dir, tmp_path):
    """Mutation — flipping derived:false on an export must not resurrect vault
    memory as an obsidian[] peer hit (R6 membership)."""
    service = _both(vault_dir, tmp_path)
    needle = "derived-false-mutation-needle-662"
    written = service.upsert_memory(
        title="Export flipped",
        body=needle,
        workspace_slug="lg",
    )
    path = vault_dir / written["obsidian"]["path"]
    text = path.read_text(encoding="utf-8")
    path.write_text(text.replace("derived: true", "derived: false"), encoding="utf-8")

    found = service.search(needle, workspace_slug="lg")
    assert all(row.get("note_type") != "memory" for row in found["obsidian"])
    assert any(row.get("id") == written["graph"]["id"] for row in found["graph"])


def test_r5_checkpoint_overlap_never_enters_recall(vault_dir, tmp_path):
    """Assumption check — checkpoints stay vault-native and out of durable recall."""
    service = _both(vault_dir, tmp_path)
    service.append_checkpoint(
        ticket_id="t-cp",
        workspace_slug="lg",
        run_id="run-adv",
        entry="### Assumption\ntrusted server throttle checkpoint only",
    )
    service._graph_for_workspace("lg").upsert_node(
        title="Graph policy",
        body="trusted server throttle in graph",
        workspace_slug="lg",
        node_type="memory",
    )
    ranked = service.recall_related("trusted server throttle", workspace_slug="lg")
    assert ranked
    assert all(row["source"] == "sqlite" for row in ranked)
    assert all("checkpoint" not in (row.get("note_type") or "") for row in ranked)
    assert all(row["title"] != "Assumption" for row in ranked)


def test_r5_learning_vault_hand_edit_cannot_outrank_graph(vault_dir, tmp_path):
    """Mutation — learning hand-edits are as inert as memory hand-edits for recall."""
    service = _both(vault_dir, tmp_path)
    written = service.append_learning(
        ticket_id="t-learn",
        workspace_slug="lg",
        content="trusted server throttle",
    )
    export_path = vault_dir / written["obsidian"]["path"]
    export_path.write_text(
        export_path.read_text(encoding="utf-8").replace(
            "trusted server throttle",
            "trusted server throttle trusted server throttle trusted server",
        ),
        encoding="utf-8",
    )
    service.obsidian.upsert_note(
        title="Vault learning ghost",
        body="trusted server throttle trusted server throttle",
        workspace_slug="lg",
        note_type="learning",
    )
    ranked = service.recall_related("trusted server throttle", workspace_slug="lg")
    assert [row["id"] for row in ranked] == [written["graph"]["id"]]
    assert ranked[0]["source"] == "sqlite"


# ---------------------------------------------------------------------------
# R4 — orphan edges: missing id, learning tree, cross-workspace, idempotence
# ---------------------------------------------------------------------------


def test_r4_rebuild_quarantines_vault_file_with_missing_id(vault_dir, tmp_path):
    """Corrupt input — no frontmatter id means absent from graph → quarantine."""
    from loregarden.services.memory_export import rebuild_workspace_exports

    service = _both(vault_dir, tmp_path)
    ghost = _write_raw_vault_note(
        vault_dir,
        subdir="Memory",
        workspace="lg",
        filename="no-id-ghost.md",
        frontmatter={"title": "No id ghost", "type": "memory", "derived": False},
        body="missing-id-orphan-needle-662",
    )
    assert ghost.is_file()
    service._graph_for_workspace("lg").upsert_node(
        title="Keeper",
        body="kept",
        workspace_slug="lg",
        node_type="memory",
    )

    rebuild_workspace_exports(service, workspace_slug="lg")

    assert not ghost.is_file()
    orphan_root = vault_dir / "Loregarden" / "Memory" / "_orphans" / "lg"
    assert list(orphan_root.rglob("*.md")), "missing-id file must move under _orphans, not delete"
    found = service.search("missing-id-orphan-needle-662", workspace_slug="lg")
    assert found["obsidian"] == []
    assert found["graph"] == []


def test_r4_learning_orphan_lands_under_learnings_orphans(vault_dir, tmp_path):
    """Structure — learning ghosts quarantine under Learnings/_orphans/{ws}/."""
    from loregarden.services.memory_export import rebuild_workspace_exports

    service = _both(vault_dir, tmp_path)
    ghost = service.obsidian.upsert_note(
        note_id="learning-orphan-id",
        title="Orphan learning",
        body="learning-orphan-body-662",
        workspace_slug="lg",
        note_type="learning",
    )
    ghost_path = vault_dir / ghost.path
    rebuild_workspace_exports(service, workspace_slug="lg")

    assert not ghost_path.is_file()
    learn_orphans = vault_dir / "Loregarden" / "Learnings" / "_orphans" / "lg"
    assert list(learn_orphans.rglob("*.md"))
    mem_orphans = vault_dir / "Loregarden" / "Memory" / "_orphans" / "lg"
    assert not list(mem_orphans.rglob("*.md"))


def test_r4_rebuild_is_workspace_scoped(vault_dir, tmp_path):
    """Isolation — rebuilding lg must not quarantine or rewrite other-ws files."""
    from loregarden.services.memory_export import rebuild_workspace_exports

    service = _both(vault_dir, tmp_path)
    other = service.obsidian.upsert_note(
        note_id="other-ws-only",
        title="Other workspace memory",
        body="other-ws-needle-662",
        workspace_slug="other",
        note_type="memory",
    )
    other_path = vault_dir / other.path
    service._graph_for_workspace("lg").upsert_node(
        title="Lg only",
        body="lg body",
        workspace_slug="lg",
        node_type="memory",
    )

    rebuild_workspace_exports(service, workspace_slug="lg")

    assert other_path.is_file(), "other workspace vault notes must be left alone"
    assert "other-ws-needle-662" in other_path.read_text(encoding="utf-8")
    other_orphans = vault_dir / "Loregarden" / "Memory" / "_orphans" / "other"
    assert not list(other_orphans.rglob("*.md"))


def test_r4_rebuild_idempotent_does_not_delete_prior_orphans(vault_dir, tmp_path):
    """Order dependency — second rebuild must keep quarantined files readable."""
    from loregarden.services.memory_export import rebuild_workspace_exports

    service = _both(vault_dir, tmp_path)
    service.obsidian.upsert_note(
        note_id="idempotent-orphan",
        title="Idempotent orphan",
        body="idempotent-orphan-body",
        workspace_slug="lg",
        note_type="memory",
    )
    service._graph_for_workspace("lg").upsert_node(
        title="Live",
        body="live",
        workspace_slug="lg",
        node_type="memory",
    )
    rebuild_workspace_exports(service, workspace_slug="lg")
    orphan_root = vault_dir / "Loregarden" / "Memory" / "_orphans" / "lg"
    first = list(orphan_root.rglob("*.md"))
    assert first
    first_text = {p.read_text(encoding="utf-8") for p in first}

    rebuild_workspace_exports(service, workspace_slug="lg")
    second = list(orphan_root.rglob("*.md"))
    assert second, "orphans must not be deleted on a second rebuild"
    assert {p.read_text(encoding="utf-8") for p in second} == first_text


def test_r4_rebuild_exports_match_graph_count_under_stress(vault_dir, tmp_path):
    """Stress — every graph memory+learning projects; no silent truncation."""
    from loregarden.services.memory_export import rebuild_workspace_exports

    service = _both(vault_dir, tmp_path)
    graph = service._graph_for_workspace("lg")
    n = 40
    for i in range(n):
        graph.upsert_node(
            title=f"Mem {i}",
            body=f"body mem {i}",
            workspace_slug="lg",
            node_type="memory",
        )
        graph.upsert_node(
            title=f"Learn {i}",
            body=f"body learn {i}",
            workspace_slug="lg",
            node_type="learning",
            ticket_id=f"t-{i}",
        )

    summary = rebuild_workspace_exports(service, workspace_slug="lg")
    assert summary["exported"] >= 2 * n

    mem_files = [
        p
        for p in (vault_dir / "Loregarden" / "Memory" / "lg").rglob("*.md")
        if "_orphans" not in p.parts
    ]
    learn_files = [
        p
        for p in (vault_dir / "Loregarden" / "Learnings" / "lg").rglob("*.md")
        if "_orphans" not in p.parts
    ]
    assert len(mem_files) >= n
    assert len(learn_files) >= n
    assert all(_frontmatter(p).get("derived") is True for p in mem_files + learn_files)


# ---------------------------------------------------------------------------
# R6 — search under failure / peer ghosts
# ---------------------------------------------------------------------------


def test_r6_search_excludes_vault_memory_even_when_graph_returns_empty(vault_dir, tmp_path):
    """Error handling — empty/unavailable graph must not fall back to vault memory peers."""
    service = _both(vault_dir, tmp_path)
    needle = "graph-empty-vault-peer-662"
    service.obsidian.upsert_note(
        title="Vault peer",
        body=needle,
        workspace_slug="lg",
        note_type="memory",
    )
    service.upsert_blog_post(
        ticket_id="t-blog",
        workspace_slug="lg",
        title="Blog still searchable",
        body=needle,
    )

    with patch.object(service._graph_for_workspace("lg"), "search", return_value=[]):
        found = service.search(needle, workspace_slug="lg")
    assert found["graph"] == []
    assert all(row.get("note_type") != "memory" for row in found["obsidian"])
    assert all(row.get("note_type") != "learning" for row in found["obsidian"])
    assert any(row.get("note_type") == "blog_post" for row in found["obsidian"])


def test_r6_precutover_vault_memory_peer_absent_from_obsidian_hits(vault_dir, tmp_path):
    """Assumption — leftover dual-write vault copies must not populate obsidian[]."""
    service = _both(vault_dir, tmp_path)
    needle = "precutover-peer-needle-662"
    written = service.upsert_memory(title="Graph record", body=needle, workspace_slug="lg")
    # Simulate the historical dual-uuid vault peer sitting beside the export.
    service.obsidian.upsert_note(
        note_id="different-uuid-peer",
        title="Peer copy",
        body=needle,
        workspace_slug="lg",
        note_type="memory",
    )

    found = service.search(needle, workspace_slug="lg")
    assert all(row.get("note_type") != "memory" for row in found["obsidian"])
    assert {row.get("id") for row in found["graph"]} == {written["graph"]["id"]}


# ---------------------------------------------------------------------------
# R7 — learning role copy (protocol embed companion)
# ---------------------------------------------------------------------------


def test_r7_learning_role_drops_peer_dual_write_language():
    """R7 companion — learning agent role must not teach vault+graph peer dual-write."""
    root = Path(__file__).resolve().parents[2]
    role = root / "agent_context" / "agents" / "9_learning" / "learning_v1.md"
    text = role.read_text(encoding="utf-8").casefold()
    # Peer framing that the cutover retires.
    assert "dual-writes" not in text and "dual-write" not in text
    assert "graph" in text
    assert "export" in text or "record" in text or "derived" in text


# ---------------------------------------------------------------------------
# R1 / R8 — authority + telemetry mutations
# ---------------------------------------------------------------------------


def test_r1_relation_vault_role_is_not_export_or_record_peer():
    """Relations never lived in the vault — role must not claim vault export."""
    from loregarden.services import memory_authority

    assert memory_authority.authoritative_store("relation") == MemoryStoreKind.GRAPH
    # Relations have no vault surface; vault_role should refuse or return a
    # non-export/non-record sentinel rather than implying a markdown twin.
    try:
        role = memory_authority.vault_role("relation")
    except (ValueError, KeyError):
        return
    assert role not in {"export", "record"}


def test_r8_briefing_store_states_do_not_mark_vault_read_for_durable_hits(tmp_path):
    """Mutation — durable memory hits must credit GRAPH; VAULT must not flip to
    READ solely because a memory note matched (checkpoints may still READ)."""
    from loregarden.agents.inherited_wisdom import build_inherited_wisdom
    from loregarden.models.domain import MemoryStoreState
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
    ticket = briefing_ticket(title="Cap how fast a trusted MCP server can be called")
    result = build_inherited_wisdom(ticket, "lg", memory=service)

    assert result.learnings_injected >= 1
    assert result.store_states[MemoryStoreKind.GRAPH] == MemoryStoreState.READ
    # VAULT may be readiness-READ for checkpoints, but must not be the durable
    # recall credit. Pin that GRAPH is in the recall set and VAULT is not.
    from loregarden.agents import inherited_wisdom as iw

    assert MemoryStoreKind.GRAPH in iw._RECALL_STORES
    assert MemoryStoreKind.VAULT not in iw._RECALL_STORES
