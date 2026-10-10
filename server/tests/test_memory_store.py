import json
from pathlib import Path
from unittest.mock import patch

import pytest
from loregarden.models.domain import MemoryRelationType
from loregarden.services.memory_store import (
    CHECKPOINT_ENTRY_DELIMITER,
    AgentMemoryService,
    MemoryGraphStore,
    ObsidianMemoryStore,
)
from tests.memory_helpers import frozen_clock


@pytest.fixture
def vault_dir(tmp_path):
    vault = tmp_path / "vault"
    vault.mkdir()
    return vault


def test_obsidian_append_learning_writes_workspace_subdir(vault_dir):
    store = ObsidianMemoryStore(vault_dir)
    note = store.append_learning(
        ticket_id="feat-memory",
        workspace_slug="loregarden",
        content="Always use DELETE journal on iCloud SQLite.",
        tags=["sqlite"],
    )
    path = vault_dir / note.path
    assert path.is_file()
    assert "loregarden" in str(path)
    text = path.read_text(encoding="utf-8")
    assert text.startswith("---\n")
    assert 'type: "learning"' in text
    assert "feat-memory" in text
    assert "DELETE journal" in text


def test_obsidian_append_learning_writes_frontmatter_note(vault_dir):
    store = ObsidianMemoryStore(vault_dir)
    note = store.append_learning(
        ticket_id="feat-memory",
        workspace_slug="loregarden",
        content="Always use DELETE journal on iCloud SQLite.",
        tags=["sqlite"],
    )
    path = vault_dir / note.path
    assert path.is_file()
    text = path.read_text(encoding="utf-8")
    assert text.startswith("---\n")
    assert 'type: "learning"' in text
    assert "feat-memory" in text
    assert "DELETE journal" in text


def test_obsidian_upsert_blog_post_writes_workspace_subdir(vault_dir):
    store = ObsidianMemoryStore(vault_dir)
    note = store.upsert_blog_post(
        ticket_id="feat-blog",
        workspace_slug="loregarden",
        title="Shipping workspace memory",
        body="We organized memory per workspace.",
        tags=["retrospective"],
    )
    path = vault_dir / note.path
    assert path.is_file()
    assert "BlogPosts" in str(path)
    assert "loregarden" in str(path)
    text = path.read_text(encoding="utf-8")
    assert 'type: "blog_post"' in text
    assert "feat-blog" in text


def test_obsidian_append_checkpoint_writes_workspace_subdir(vault_dir):
    store = ObsidianMemoryStore(vault_dir)
    result = store.append_checkpoint(
        workspace_slug="loregarden",
        ticket_id="feat-checkpoint",
        run_id="2026-06-16T10-00-00Z-spec",
        entry="### [feat-checkpoint] Spec — ambiguous field\n**Confidence:** Medium",
    )
    path = vault_dir / result["path"]
    assert path.is_file()
    assert "Checkpoints" in str(path)
    assert "loregarden" in str(path)
    assert "feat-checkpoint" in str(path)
    text = path.read_text(encoding="utf-8")
    assert text.startswith("---\n")
    assert 'type: "checkpoint"' in text
    assert "ambiguous field" in text


def test_obsidian_append_checkpoint_accumulates_entries_in_one_file(vault_dir):
    store = ObsidianMemoryStore(vault_dir)
    first = store.append_checkpoint(
        workspace_slug="loregarden",
        ticket_id="feat-checkpoint",
        run_id="run-1",
        entry="First entry.",
    )
    second = store.append_checkpoint(
        workspace_slug="loregarden",
        ticket_id="feat-checkpoint",
        run_id="run-1",
        entry="Second entry.",
    )
    assert first["path"] == second["path"]
    path = vault_dir / first["path"]
    text = path.read_text(encoding="utf-8")
    assert "First entry." in text
    assert "Second entry." in text
    # A different run_id for the same ticket gets its own file.
    other_run = store.append_checkpoint(
        workspace_slug="loregarden",
        ticket_id="feat-checkpoint",
        run_id="run-2",
        entry="Other run entry.",
    )
    assert other_run["path"] != first["path"]


def test_obsidian_append_checkpoint_marks_each_entry_boundary(vault_dir):
    """Each entry is introduced by the reserved marker, whatever it contains.

    The boundary used to be a blank line, which an entry may contain — so a
    multi-paragraph checkpoint was read back as several, and the fragments spent
    the stage briefing's slots. `_split_entries` in `agents/inherited_wisdom`
    reads what this writes.
    """
    store = ObsidianMemoryStore(vault_dir)
    written = store.append_checkpoint(
        workspace_slug="loregarden",
        ticket_id="feat-checkpoint",
        run_id="run-1",
        entry="### heading\n\n**Assumption made:** the conservative one",
    )
    store.append_checkpoint(
        workspace_slug="loregarden",
        ticket_id="feat-checkpoint",
        run_id="run-1",
        entry="A second entry.",
    )
    text = (vault_dir / written["path"]).read_text(encoding="utf-8")
    assert text.count(CHECKPOINT_ENTRY_DELIMITER) == 2
    # Leading, not trailing: the marker must precede the entry it introduces, or
    # the text before the first one cannot be told from a pre-marker legacy log.
    assert f"{CHECKPOINT_ENTRY_DELIMITER}\n\n### heading" in text


def test_obsidian_append_checkpoint_refuses_an_entry_carrying_the_marker(vault_dir):
    """Loud rather than lenient: an entry holding the marker would split itself,
    and the halves would look exactly like two checkpoints someone wrote."""
    store = ObsidianMemoryStore(vault_dir)
    with pytest.raises(ValueError, match="reserved as the entry separator"):
        store.append_checkpoint(
            workspace_slug="loregarden",
            ticket_id="feat-checkpoint",
            run_id="run-1",
            entry=f"Decided X.\n\n{CHECKPOINT_ENTRY_DELIMITER}\n\nDecided Y.",
        )


def test_agent_memory_service_append_checkpoint_obsidian_only(vault_dir):
    service = AgentMemoryService(obsidian=ObsidianMemoryStore(vault_dir))
    result = service.append_checkpoint(
        ticket_id="feat-checkpoint",
        workspace_slug="loregarden",
        run_id="run-1",
        entry="Checkpoint via facade.",
    )
    assert "obsidian" in result
    assert "graph" not in result
    path = vault_dir / result["obsidian"]["path"]
    assert "Checkpoint via facade." in path.read_text(encoding="utf-8")


def test_agent_memory_service_append_checkpoint_requires_obsidian():
    service = AgentMemoryService(obsidian=None)
    with pytest.raises(ValueError, match="Obsidian vault"):
        service.append_checkpoint(
            ticket_id="feat-checkpoint",
            workspace_slug="loregarden",
            run_id="run-1",
            entry="No backend configured.",
        )


def test_obsidian_search_scoped_to_workspace(vault_dir):
    store = ObsidianMemoryStore(vault_dir)
    store.upsert_note(
        title="Loregarden pattern",
        body="Scoped to loregarden workspace.",
        workspace_slug="loregarden",
    )
    store.upsert_note(
        title="Other pattern",
        body="Scoped to other workspace.",
        workspace_slug="other",
    )
    hits = store.search("pattern", workspace_slug="loregarden")
    assert len(hits) == 1
    assert hits[0].title == "Loregarden pattern"


def test_obsidian_search_finds_note(vault_dir):
    store = ObsidianMemoryStore(vault_dir)
    store.upsert_note(
        title="Permission bridge timeout",
        body="Default timeout is 3600 seconds.",
        tags=["approvals"],
    )
    hits = store.search("permission bridge")
    assert len(hits) == 1
    assert hits[0].title == "Permission bridge timeout"


def test_memory_graph_workspace_scoped_db(tmp_path):
    base = tmp_path / "Loregarden" / "memory.db"
    ws_a = MemoryGraphStore(base.parent / "loregarden" / base.name)
    ws_b = MemoryGraphStore(base.parent / "other" / base.name)
    ws_a.upsert_node(title="Pattern A", body="Workspace A only.", workspace_slug="loregarden")
    ws_b.upsert_node(title="Pattern B", body="Workspace B only.", workspace_slug="other")
    assert len(ws_a.search("Pattern", workspace_slug="loregarden")) == 1
    assert len(ws_b.search("Pattern", workspace_slug="other")) == 1
    assert len(ws_a.search("Pattern", workspace_slug="other")) == 0


def test_memory_graph_upsert_and_relation(tmp_path):
    db_path = tmp_path / "memory.db"
    graph = MemoryGraphStore(db_path)
    a = graph.upsert_node(title="Pattern A", body="Use MCP for workflow state.")
    b = graph.upsert_node(title="Pattern B", body="Do not edit WORKFLOW STATE in markdown.")
    rel = graph.create_relation(
        source_id=a["id"], target_id=b["id"], relation_type=MemoryRelationType.SUPPORTS
    )
    assert rel["source_id"] == a["id"]
    assert rel["target_id"] == b["id"]
    hits = graph.search("MCP for workflow")
    assert len(hits) == 1
    assert hits[0]["title"] == "Pattern A"


def test_memory_graph_uses_delete_journal_in_icloud(tmp_path, monkeypatch):
    icloud = tmp_path / "icloud"
    icloud.mkdir()
    monkeypatch.setattr("loregarden.config.settings.icloud_root", str(icloud))
    db_path = icloud / "Loregarden" / "memory.db"
    graph = MemoryGraphStore(db_path)
    graph.upsert_node(title="icloud note", body="sync-safe")
    with graph._connect() as conn:
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
    assert mode.lower() == "delete"


def test_agent_memory_service_graph_then_export_shared_id(vault_dir, tmp_path):
    """Cutover R2/R6/R9 — GRAPH is the record; vault is labelled export.

    Replaces the peer dual-write assertion: learning lands in graph[], not as a
    vault memory/learning peer in obsidian[], and ids match.
    """
    service = AgentMemoryService(
        obsidian=ObsidianMemoryStore(vault_dir),
        graph_sqlite_base=tmp_path / "Loregarden" / "memory.db",
    )
    result = service.append_learning(
        ticket_id="t-01",
        workspace_slug="loregarden",
        content="Graph-then-export learning test.",
    )
    assert "obsidian" in result
    assert "graph" in result
    assert result["obsidian"]["id"] == result["graph"]["id"]
    assert "loregarden" in result["obsidian"]["path"]
    search = service.search("Graph-then-export", workspace_slug="loregarden")
    assert len(search["graph"]) == 1
    assert search["obsidian"] == []
    other_search = service.search("Graph-then-export", workspace_slug="other")
    assert len(other_search["obsidian"]) == 0
    assert len(other_search["graph"]) == 0


def test_mcp_memory_tools(client, vault_dir, tmp_path, monkeypatch):
    monkeypatch.setattr("loregarden.config.settings.obsidian_vault_dir", str(vault_dir))
    monkeypatch.setattr(
        "loregarden.config.settings.memory_sqlite_url",
        f"sqlite:///{tmp_path / 'mcp-memory.db'}",
    )

    from loregarden.db.session import engine
    from loregarden.mcp.tools import execute_tool
    from sqlmodel import Session

    with Session(engine) as session:
        status = json.loads(execute_tool(session, "loregarden_memory_status", {}))
        assert status["enabled"] is True
        assert status["obsidian_vault"] == str(vault_dir.resolve())

        scoped = json.loads(
            execute_tool(
                session,
                "loregarden_memory_status",
                {"workspace_slug": "loregarden"},
            )
        )
        assert scoped["workspace_slug"] == "loregarden"
        assert scoped["obsidian_memory_dir"].endswith("Loregarden/Memory/loregarden")
        assert scoped["obsidian_learnings_dir"].endswith("Loregarden/Learnings/loregarden")
        assert scoped["obsidian_blogposts_dir"].endswith("Loregarden/BlogPosts/loregarden")
        assert scoped["obsidian_checkpoints_dir"].endswith("Loregarden/Checkpoints/loregarden")
        assert "loregarden" in scoped["memory_sqlite_path"]
        assert scoped["memory_sqlite_path"].endswith("mcp-memory.db")
        assert scoped["memory_graph_tables"] == ["memory_nodes", "memory_relations"]
        assert scoped["memory_graph_node_types"] == ["memory", "learning"]
        assert scoped["memory_graph_excludes"] == ["blog_post", "checkpoint"]

        blog = json.loads(
            execute_tool(
                session,
                "loregarden_upsert_blog_post",
                {
                    "ticket_id": "feat-memory",
                    "workspace_slug": "loregarden",
                    "title": "Memory setup retrospective",
                    "body": "Workspace-scoped paths for memory, learnings, and blog posts.",
                },
            )
        )
        assert "obsidian" in blog
        assert "BlogPosts" in blog["obsidian"]["path"]

        upsert = json.loads(
            execute_tool(
                session,
                "loregarden_upsert_memory",
                {
                    "title": "Checkpoint protocol",
                    "body": "Subagents write scoped logs only.",
                    "tags": ["workflow"],
                    "workspace_slug": "loregarden",
                },
            )
        )
        assert "obsidian" in upsert
        assert "graph" in upsert
        assert upsert["obsidian"]["id"] == upsert["graph"]["id"]

        search = json.loads(
            execute_tool(
                session,
                "loregarden_search_memory",
                {"query": "checkpoint", "workspace_slug": "loregarden"},
            )
        )
        # Cutover R6: durable memory is graph-only; vault export peers are filtered.
        assert len(search["graph"]) >= 1
        assert all(row.get("note_type") != "memory" for row in search["obsidian"])
        assert all(row.get("node_type") in {"memory", "learning"} for row in search["graph"])

        checkpoint = json.loads(
            execute_tool(
                session,
                "loregarden_append_checkpoint",
                {
                    "ticket_id": "feat-memory",
                    "workspace_slug": "loregarden",
                    "run_id": "2026-06-16T10-00-00Z-spec",
                    "entry": "### [feat-memory] Spec — ambiguous field name\n"
                    "**Would have asked:** singular or plural?\n"
                    "**Assumption made:** plural\n"
                    "**Confidence:** Medium",
                },
            )
        )
        assert "obsidian" in checkpoint
        assert "Checkpoints" in checkpoint["obsidian"]["path"]
        # A second entry for the same ticket+run appends to the same file.
        checkpoint2 = json.loads(
            execute_tool(
                session,
                "loregarden_append_checkpoint",
                {
                    "ticket_id": "feat-memory",
                    "workspace_slug": "loregarden",
                    "run_id": "2026-06-16T10-00-00Z-spec",
                    "entry": "### [feat-memory] Spec — second ambiguity\n"
                    "**Would have asked:** another question\n"
                    "**Assumption made:** conservative default\n"
                    "**Confidence:** High",
                },
            )
        )
        assert checkpoint["obsidian"]["path"] == checkpoint2["obsidian"]["path"]
        checkpoint_path = vault_dir / checkpoint["obsidian"]["path"]
        checkpoint_text = checkpoint_path.read_text(encoding="utf-8")
        assert "ambiguous field name" in checkpoint_text
        assert "second ambiguity" in checkpoint_text


def test_memory_api_status(client, vault_dir, monkeypatch):
    monkeypatch.setattr("loregarden.config.settings.obsidian_vault_dir", str(vault_dir))
    res = client.get("/api/memory/status")
    assert res.status_code == 200
    body = res.json()
    assert body["enabled"] is True
    assert body["obsidian_vault"] == str(vault_dir.resolve())


def test_memory_api_config_get_put(client, vault_dir, tmp_path, monkeypatch):
    db_path = tmp_path / "control.db"
    monkeypatch.setattr("loregarden.config.settings.repo_root", tmp_path)
    payload = {
        "icloud_root": str(vault_dir.parent),
        "obsidian_vault_dir": str(vault_dir),
        "obsidian_memory_subdir": "Loregarden/Memory",
        "obsidian_learnings_subdir": "Loregarden/Learnings",
        "obsidian_blogposts_subdir": "Loregarden/BlogPosts",
        "memory_sqlite_url": f"sqlite:///{tmp_path / 'memory.db'}",
        "database_url": f"sqlite:///{db_path}",
    }
    res = client.put("/api/memory/config", json=payload)
    assert res.status_code == 200
    body = res.json()
    assert body["config"]["obsidian_vault_dir"] == str(vault_dir)
    assert body["status"]["enabled"] is True
    assert (tmp_path / "data" / "memory.local.json").is_file()

    get_res = client.get("/api/memory/config")
    assert get_res.status_code == 200
    assert get_res.json()["config"]["obsidian_vault_dir"] == str(vault_dir)


def test_memory_api_config_rejects_bad_vault(client):
    res = client.put(
        "/api/memory/config",
        json={
            "icloud_root": "",
            "obsidian_vault_dir": "/no/such/vault",
            "obsidian_memory_subdir": "Loregarden/Memory",
            "obsidian_learnings_subdir": "Loregarden/Learnings",
            "memory_sqlite_url": "",
            "database_url": "sqlite:///data/loregarden.db",
        },
    )
    assert res.status_code == 400


def test_sqlite_db_in_icloud_dir(tmp_path, monkeypatch):
    import os
    import subprocess
    import sys

    icloud = tmp_path / "Mobile Documents" / "com~apple~CloudDocs"
    icloud.mkdir(parents=True)
    db_path = icloud / "Loregarden" / "loregarden.db"
    repo = tmp_path / "repo"
    repo.mkdir()

    server_dir = Path(__file__).resolve().parents[1]
    env = os.environ.copy()
    env["LOREGARDEN_REPO_ROOT"] = str(repo)
    env["LOREGARDEN_ICLOUD_ROOT"] = str(icloud)
    env["LOREGARDEN_DATABASE_URL"] = f"sqlite:///{db_path}"

    proc = subprocess.run(
        [sys.executable, "-m", "loregarden.cli.init_db", "--empty"],
        cwd=str(server_dir),
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    assert db_path.is_file()

    from loregarden.services.path_resolve import resolve_sqlite_path, sqlite_url_for_path
    from sqlmodel import create_engine

    eng = create_engine(
        sqlite_url_for_path(resolve_sqlite_path(env["LOREGARDEN_DATABASE_URL"], repo)),
        connect_args={"check_same_thread": False, "timeout": 30.0},
    )
    with eng.connect() as conn:
        conn.exec_driver_sql("PRAGMA journal_mode=DELETE")
        mode = conn.exec_driver_sql("PRAGMA journal_mode").fetchone()[0]
    assert mode.lower() == "delete"


# ---------------------------------------------------------------------------
# R2 — MemoryGraphStore.list_nodes: enumeration surface for the graph half.
# R3 — AgentMemoryService.recall_related: GRAPH-only term-overlap read
#      (lg-improved-memory-662 cutover; vault is export, not a recall peer).
#
# The briefing path used to query both stores with the whole ticket title as one
# contiguous substring, which essentially never matched. These tests pin the
# replacement, and each names the wrong implementation it would catch.
# ---------------------------------------------------------------------------


def _both_backends(vault_dir, tmp_path) -> AgentMemoryService:
    return AgentMemoryService(
        obsidian=ObsidianMemoryStore(vault_dir),
        graph_sqlite_base=tmp_path / "Loregarden" / "memory.db",
    )


def test_graph_list_nodes_returns_every_node_unfiltered(tmp_path):
    """AC2.1 — the whole point of the new surface: enumeration, not matching.
    Catches a list_nodes that keeps any LIKE predicate, which would rank an
    already-substring-filtered list and ship the bug one layer down."""
    graph = MemoryGraphStore(tmp_path / "lg" / "memory.db")
    for title in ("Throttle policy", "Sprite batching", "Lease renewal"):
        graph.upsert_node(title=title, body="Body text.", workspace_slug="lg")

    rows = graph.list_nodes(workspace_slug="lg")
    assert {row["title"] for row in rows} == {"Throttle policy", "Sprite batching", "Lease renewal"}


def test_graph_list_nodes_is_ordered_newest_first(tmp_path):
    """AC2.2 — the ranker's recency tiebreak reads updated_at, but the 500-row
    cap means enumeration order decides which nodes are seen at all."""
    graph = MemoryGraphStore(tmp_path / "lg" / "memory.db")
    with frozen_clock("2026-01-01T00:00:00+00:00", "2026-02-01T00:00:00+00:00"):
        graph.upsert_node(title="Older", body="Body.", workspace_slug="lg")
        graph.upsert_node(title="Newer", body="Body.", workspace_slug="lg")

    assert [row["title"] for row in graph.list_nodes(workspace_slug="lg")] == ["Newer", "Older"]


def test_graph_list_nodes_honours_its_limit(tmp_path):
    """AC2.3 — catches a list_nodes that drops the LIMIT clause, which would let
    the graph half become the cost centre the Obsidian half is capped against."""
    graph = MemoryGraphStore(tmp_path / "lg" / "memory.db")
    for index in range(4):
        graph.upsert_node(title=f"Node {index}", body="Body.", workspace_slug="lg")

    assert len(graph.list_nodes(workspace_slug="lg", limit=2)) == 2


def test_graph_list_nodes_excludes_other_workspaces(tmp_path):
    """AC2.4 — catches a list_nodes that drops the workspace_slug branch and
    leaks another workspace's memory into this workspace's briefings."""
    base = tmp_path / "Loregarden" / "memory.db"
    graph = MemoryGraphStore(base.parent / "lg" / base.name)
    graph.upsert_node(title="Ours", body="Body.", workspace_slug="lg")
    graph.upsert_node(title="Theirs", body="Body.", workspace_slug="other")

    assert [row["title"] for row in graph.list_nodes(workspace_slug="lg")] == ["Ours"]


def test_graph_list_nodes_rows_match_search_rows(tmp_path):
    """AC2.5 — the ranker and the projection in recall_related read the keys
    search() already returns; a row shaped differently (or with tags left as raw
    JSON) breaks them at runtime, not at import."""
    graph = MemoryGraphStore(tmp_path / "lg" / "memory.db")
    graph.upsert_node(title="Throttle policy", body="Body.", tags=["ops"], workspace_slug="lg")

    listed = graph.list_nodes(workspace_slug="lg")[0]
    searched = graph.search("Throttle policy", workspace_slug="lg")[0]
    assert set(listed) == set(searched)
    assert listed["tags"] == ["ops"]


def test_graph_search_still_matches_only_substrings(tmp_path):
    """AC2.6 — guard. search() is the loregarden_search_memory tool path, where
    agents pass short keywords and substring is correct. Catches a change that
    'helpfully' upgrades it too, which is outside this ticket's narrow arm."""
    graph = MemoryGraphStore(tmp_path / "lg" / "memory.db")
    graph.upsert_node(title="Throttle policy", body="Cap the call rate.", workspace_slug="lg")
    graph.upsert_node(title="Sprite batching", body="Unrelated.", workspace_slug="lg")

    assert [row["title"] for row in graph.search("Cap the call", workspace_slug="lg")] == [
        "Throttle policy"
    ]
    assert graph.search("throttle sprite", workspace_slug="lg") == []


@pytest.mark.parametrize("query", ["", "   ", "the that with this"])
def test_recall_related_returns_nothing_at_zero_io_for_empty_queries(vault_dir, tmp_path, query):
    """AC3.1 / AC4 — an empty or all-stopword query must return [] BEFORE
    touching a store. Asserting only on the result cannot tell that apart from a
    full 500-note vault read that happened to match nothing.

    BOTH stores are counted. The graph read is the cost this ticket newly adds —
    up to 500 rows out of a per-workspace SQLite file, opened per prompt build —
    so an implementation that returns early only after opening the graph and
    calling list_nodes passes an Obsidian-only assertion while doing exactly the
    I/O S1 exists to avoid."""
    service = _both_backends(vault_dir, tmp_path)
    service.obsidian.upsert_note(title="Throttle policy", body="Body.", workspace_slug="lg")

    with (
        patch.object(
            service.obsidian, "list_notes", wraps=service.obsidian.list_notes
        ) as list_notes,
        patch.object(MemoryGraphStore, "list_nodes") as list_nodes,
    ):
        assert service.recall_related(query, workspace_slug="lg") == []
    assert list_notes.call_count == 0
    assert list_nodes.call_count == 0


def test_recall_related_reads_graph_and_ignores_vault_memory(vault_dir, tmp_path):
    """AC3.2 / Cutover R5 — durable recall is GRAPH-only. A vault memory peer
    that would have ranked under the old both-stores path must not appear."""
    service = _both_backends(vault_dir, tmp_path)
    service.obsidian.upsert_note(
        title="Trusted server throttle",
        body="Cap the call rate per tool.",
        workspace_slug="lg",
        note_type="memory",
    )
    graph = service._graph_for_workspace("lg")
    graph.upsert_node(
        title="Retry budget",
        body="A throttled server returns before the trusted retry loop runs.",
        workspace_slug="lg",
        node_type="memory",
    )

    ranked = service.recall_related("trusted server", workspace_slug="lg")
    assert [row["title"] for row in ranked] == ["Retry budget"]
    assert all(row["source"] == "sqlite" for row in ranked)


def test_recall_related_works_with_the_graph_alone(tmp_path):
    """AC3.3 — obsidian=None is the real deployment shape for a machine with no
    vault; the graph must still be ranked."""
    service = AgentMemoryService(obsidian=None, graph_sqlite_base=tmp_path / "LG" / "memory.db")
    service._graph_for_workspace("lg").upsert_node(
        title="Retry budget",
        body="A throttled server returns before the trusted retry loop runs.",
        workspace_slug="lg",
    )

    ranked = service.recall_related("trusted server retry", workspace_slug="lg")
    assert [row["title"] for row in ranked] == ["Retry budget"]


def test_recall_related_ignores_vault_only_memory_notes(vault_dir):
    """AC3.3 / Cutover R5 — vault-only memory is no longer a recall source.

    Writes of memory/learning without a graph fail closed (R2); leftover vault
    notes from before cutover must not leak into recall either.
    """
    service = AgentMemoryService(obsidian=ObsidianMemoryStore(vault_dir), graph_sqlite_base=None)
    service.obsidian.upsert_note(
        title="Trusted server throttle",
        body="Cap the call rate per tool.",
        workspace_slug="lg",
        note_type="memory",
    )

    ranked = service.recall_related("trusted server", workspace_slug="lg")
    assert ranked == []


def test_recall_related_shared_id_learning_appears_once(vault_dir, tmp_path):
    """AC3.4 / Cutover R2 — shared node_id means one recall row, not a content-key
    dedupe papering over dual uuid4s."""
    service = _both_backends(vault_dir, tmp_path)
    result = service.append_learning(
        ticket_id="t-01",
        workspace_slug="lg",
        content="Throttle the trusted server before the retry loop consumes the budget.",
        title="Learning — t-01",
    )
    assert result["obsidian"]["id"] == result["graph"]["id"]

    ranked = service.recall_related("trusted server throttle", workspace_slug="lg")
    assert [row["title"] for row in ranked] == ["Learning — t-01"]
    assert ranked[0]["id"] == result["graph"]["id"]
    assert ranked[0]["source"] == "sqlite"


def test_recall_related_ranks_graph_nodes_only(vault_dir, tmp_path):
    """AC3.5 / Cutover R5 — vault notes are not in the candidate pool, so ranking
    is among graph nodes alone."""
    service = _both_backends(vault_dir, tmp_path)
    service.obsidian.upsert_note(
        title="Weekly notes",
        body="A throttled endpoint came up.",
        workspace_slug="lg",
        note_type="memory",
    )
    service._graph_for_workspace("lg").upsert_node(
        title="Retry budget",
        body="A throttled server returns before the trusted retry loop runs.",
        workspace_slug="lg",
        node_type="memory",
    )
    service._graph_for_workspace("lg").upsert_node(
        title="Weak throttle mention",
        body="throttled once.",
        workspace_slug="lg",
        node_type="memory",
    )

    ranked = service.recall_related("trusted server throttled", workspace_slug="lg")
    assert [row["title"] for row in ranked] == ["Retry budget", "Weak throttle mention"]
    assert all(row["source"] == "sqlite" for row in ranked)


def test_recall_related_ranks_a_newer_graph_node_above_an_equally_matching_older_one(
    vault_dir, tmp_path
):
    """AC3.5 / AC2 — the recency tiebreak among GRAPH candidates."""
    service = _both_backends(vault_dir, tmp_path)
    with frozen_clock("2026-01-01T00:00:00+00:00"):
        service._graph_for_workspace("lg").upsert_node(
            title="Older throttle node", body="trusted server", workspace_slug="lg"
        )
    with frozen_clock("2026-02-01T00:00:00+00:00"):
        service._graph_for_workspace("lg").upsert_node(
            title="Newer throttle node", body="trusted server", workspace_slug="lg"
        )

    ranked = service.recall_related("trusted server", workspace_slug="lg")
    assert [row["title"] for row in ranked] == ["Newer throttle node", "Older throttle node"]


def test_recall_related_truncates_to_its_limit_keeping_the_top_ranked(vault_dir, tmp_path):
    """AC3.6 / S6 — the `limit` parameter is otherwise never exercised: every
    other fixture in this change yields at most two candidates and every call
    site takes the default, so an implementation that ignores `limit` entirely
    passes the whole suite. End to end it is hidden too, because _memory_hits
    stops at its own _MAX_MEMORY_HITS.

    Three graph candidates with overlaps 3, 2 and 1 make the assertion independent
    of the recency tiebreak: truncation must drop the WEAKEST, not the last one
    enumerated, so a limit applied before ranking fails here as well."""
    service = _both_backends(vault_dir, tmp_path)
    graph = service._graph_for_workspace("lg")
    graph.upsert_node(title="Weakest throttle note", body="Body.", workspace_slug="lg")
    graph.upsert_node(title="Trusted server", body="Body.", workspace_slug="lg")
    graph.upsert_node(title="Trusted server throttle", body="Body.", workspace_slug="lg")

    unlimited = service.recall_related("trusted server throttle", workspace_slug="lg")
    assert [row["title"] for row in unlimited] == [
        "Trusted server throttle",
        "Trusted server",
        "Weakest throttle note",
    ]

    ranked = service.recall_related("trusted server throttle", workspace_slug="lg", limit=2)
    assert [row["title"] for row in ranked] == ["Trusted server throttle", "Trusted server"]


def test_recall_related_does_not_enumerate_obsidian(vault_dir, tmp_path):
    """AC3.6 / Cutover R5 — vault list_notes is off the durable recall path."""
    service = _both_backends(vault_dir, tmp_path)
    service._graph_for_workspace("lg").upsert_node(
        title="Trusted server throttle", body="Cap the rate.", workspace_slug="lg"
    )

    with patch.object(
        service.obsidian, "list_notes", wraps=service.obsidian.list_notes
    ) as list_notes:
        service.recall_related("trusted server", workspace_slug="lg")

    assert list_notes.call_count == 0


def test_recall_related_never_pre_filters_through_search(vault_dir, tmp_path):
    """AC3.7 — the bug itself. A recall_related that calls search() first ranks
    whatever survived the whole-query substring match, i.e. almost always
    nothing. Neither store's search may be touched."""
    service = _both_backends(vault_dir, tmp_path)
    service._graph_for_workspace("lg").upsert_node(
        title="Retry budget",
        body="A throttled server returns before the trusted retry loop runs.",
        workspace_slug="lg",
    )
    query = "Cap how fast a trusted server can be called"

    with (
        patch.object(ObsidianMemoryStore, "search") as obsidian_search,
        patch.object(MemoryGraphStore, "search") as graph_search,
    ):
        ranked = service.recall_related(query, workspace_slug="lg")

    assert obsidian_search.call_count == 0
    assert graph_search.call_count == 0
    assert len(ranked) == 1


def test_service_search_still_substring_matches_and_keeps_its_envelope(vault_dir, tmp_path):
    """AC3.8 / Cutover R6 — search() stays the loregarden_search_memory tool path;
    envelope keys unchanged; vault memory peers are not in obsidian[]."""
    service = _both_backends(vault_dir, tmp_path)
    service.upsert_memory(
        title="Trusted server throttle",
        body="Cap the call rate.",
        workspace_slug="lg",
    )
    service.upsert_blog_post(
        ticket_id="t-blog",
        workspace_slug="lg",
        title="Blog about Cap the call",
        body="Cap the call in a retrospective.",
    )

    found = service.search("Cap the call", workspace_slug="lg")
    assert set(found) == {"query", "workspace_slug", "obsidian", "graph"}
    assert [row["title"] for row in found["graph"]] == ["Trusted server throttle"]
    assert all(row.get("note_type") == "blog_post" for row in found["obsidian"])
    assert service.search("throttle rate", workspace_slug="lg")["graph"] == []


# ---------------------------------------------------------------------------
# Checkpoint discoverability via search_memory (R1–R3)
#
# Checkpoints already write to Obsidian; list_notes' default roots omit them, so
# search returns []. These tests lock: search includes checkpoints; default
# list_notes and recall_related stay checkpoint-blind; fill order prefers
# non-checkpoint hits.
# ---------------------------------------------------------------------------

_CHECKPOINT_NEEDLE = "checkpoint-discoverability-needle-718xyz"


def test_obsidian_list_notes_default_excludes_checkpoints(vault_dir):
    """R1/R3 — default list_notes (include_checkpoints omitted/False) never
    walks Checkpoints, scoped or unscoped."""
    store = ObsidianMemoryStore(vault_dir)
    store.append_checkpoint(
        workspace_slug="loregarden",
        ticket_id="feat-cp-discover",
        run_id="run-cp-default",
        entry=f"### Assumption\n{_CHECKPOINT_NEEDLE}",
    )
    store.upsert_note(
        title="Ordinary memory",
        body="Not a checkpoint.",
        workspace_slug="loregarden",
    )

    scoped = store.list_notes(workspace_slug="loregarden")
    assert all(n.note_type != "checkpoint" for n in scoped)
    assert not any("Checkpoints" in n.path for n in scoped)

    unscoped = store.list_notes()
    assert all(n.note_type != "checkpoint" for n in unscoped)
    assert not any("Checkpoints" in n.path for n in unscoped)


def test_obsidian_list_notes_include_checkpoints_walks_checkpoints_root(vault_dir):
    """R1 — include_checkpoints=True walks Checkpoints like other note roots,
    scoped and unscoped."""
    store = ObsidianMemoryStore(vault_dir)
    store.append_checkpoint(
        workspace_slug="loregarden",
        ticket_id="feat-cp-discover",
        run_id="run-cp-include",
        entry=f"### Assumption\n{_CHECKPOINT_NEEDLE}",
    )

    scoped = store.list_notes(workspace_slug="loregarden", include_checkpoints=True)
    scoped_cps = [n for n in scoped if n.note_type == "checkpoint"]
    assert len(scoped_cps) == 1
    assert "Checkpoints" in scoped_cps[0].path

    unscoped = store.list_notes(include_checkpoints=True)
    assert any(n.note_type == "checkpoint" and "Checkpoints" in n.path for n in unscoped)


def test_obsidian_search_includes_checkpoints(vault_dir):
    """R2 — ObsidianMemoryStore.search returns file-level MemoryNote hits with
    note_type=checkpoint under the Checkpoints path."""
    store = ObsidianMemoryStore(vault_dir)
    store.append_checkpoint(
        workspace_slug="loregarden",
        ticket_id="feat-cp-discover",
        run_id="run-cp-search",
        entry=(
            f"### [feat-cp-discover] plan — {_CHECKPOINT_NEEDLE}\n"
            "**Assumption made:** Keep Obsidian-only store.\n"
            "**Confidence:** High"
        ),
    )

    hits = store.search(_CHECKPOINT_NEEDLE, workspace_slug="loregarden")
    assert len(hits) == 1
    assert hits[0].note_type == "checkpoint"
    assert "Checkpoints" in hits[0].path


def test_obsidian_search_fills_non_checkpoint_hits_before_checkpoints(vault_dir):
    """R2 — when limit would starve mixed results, non-checkpoint matches fill
    first, then checkpoint hits until limit."""
    store = ObsidianMemoryStore(vault_dir)
    needle = "fill-order-needle-718abc"
    for index in range(3):
        store.upsert_note(
            title=f"Memory hit {index}",
            body=f"{needle} in memory note {index}",
            workspace_slug="loregarden",
        )
    for index in range(3):
        store.append_checkpoint(
            workspace_slug="loregarden",
            ticket_id="feat-cp-fill",
            run_id=f"run-fill-{index}",
            entry=f"### Assumption\n{needle} in checkpoint {index}",
        )

    hits = store.search(needle, workspace_slug="loregarden", limit=4)
    assert len(hits) == 4
    assert all(hit.note_type != "checkpoint" for hit in hits[:3])
    assert hits[3].note_type == "checkpoint"
    assert "Checkpoints" in hits[3].path


def test_agent_memory_service_search_includes_checkpoints(vault_dir, tmp_path):
    """R2 — AgentMemoryService.search (MCP search_memory path) inherits checkpoint
    hits via ObsidianMemoryStore.search; envelope unchanged."""
    service = _both_backends(vault_dir, tmp_path)
    service.append_checkpoint(
        ticket_id="feat-cp-discover",
        workspace_slug="loregarden",
        run_id="run-cp-svc",
        entry=f"### Assumption\n{_CHECKPOINT_NEEDLE}",
    )

    found = service.search(_CHECKPOINT_NEEDLE, workspace_slug="loregarden")
    assert set(found) == {"query", "workspace_slug", "obsidian", "graph"}
    checkpoint_hits = [
        row
        for row in found["obsidian"]
        if row["note_type"] == "checkpoint" and "Checkpoints" in row["path"]
    ]
    assert len(checkpoint_hits) == 1
    assert found["graph"] == []


def test_recall_related_stays_checkpoint_blind(vault_dir, tmp_path):
    """R3 / Cutover R5 — recall never walks vault notes (checkpoints or memory)."""
    service = _both_backends(vault_dir, tmp_path)
    blind_needle = "trusted server throttle checkpointblind718"
    service.append_checkpoint(
        ticket_id="feat-cp-blind",
        workspace_slug="loregarden",
        run_id="run-cp-blind",
        entry=f"### Assumption\nAssumption about {blind_needle}.",
    )
    service.obsidian.upsert_note(
        title="Unrelated memory",
        body="Sprite batching lease renewal.",
        workspace_slug="loregarden",
    )

    with patch.object(
        service.obsidian, "list_notes", wraps=service.obsidian.list_notes
    ) as list_notes:
        ranked = service.recall_related(blind_needle, workspace_slug="loregarden")

    assert list_notes.call_count == 0
    assert ranked == []


# ---------------------------------------------------------------------------
# Adversarial / edge mutations on R1–R3 (test-break)
#
# Designer coverage locks the happy path. These pin seams a naive
# include_checkpoints walk or shared list_notes budget would miss.
# ---------------------------------------------------------------------------


def test_obsidian_list_notes_explicit_false_excludes_checkpoints(vault_dir):
    """R1 mutation — include_checkpoints=False is identical to the default:
    Checkpoints must not appear even when the flag is passed explicitly."""
    store = ObsidianMemoryStore(vault_dir)
    store.append_checkpoint(
        workspace_slug="loregarden",
        ticket_id="feat-cp-false",
        run_id="run-cp-false",
        entry=f"### Assumption\n{_CHECKPOINT_NEEDLE}",
    )

    notes = store.list_notes(workspace_slug="loregarden", include_checkpoints=False)
    assert all(n.note_type != "checkpoint" for n in notes)
    assert not any("Checkpoints" in n.path for n in notes)


def test_obsidian_list_notes_include_checkpoints_note_type_filter(vault_dir):
    """R1 edge — note_type='checkpoint' with include_checkpoints=True returns only
    checkpoints; note_type='memory' still excludes them even when the flag is on."""
    store = ObsidianMemoryStore(vault_dir)
    store.append_checkpoint(
        workspace_slug="loregarden",
        ticket_id="feat-cp-type",
        run_id="run-cp-type",
        entry=f"### Assumption\n{_CHECKPOINT_NEEDLE}",
    )
    store.upsert_note(
        title="Ordinary memory",
        body="memory body",
        workspace_slug="loregarden",
    )

    only_cp = store.list_notes(
        workspace_slug="loregarden",
        include_checkpoints=True,
        note_type="checkpoint",
    )
    assert only_cp
    assert all(n.note_type == "checkpoint" for n in only_cp)

    only_mem = store.list_notes(
        workspace_slug="loregarden",
        include_checkpoints=True,
        note_type="memory",
    )
    assert only_mem
    assert all(n.note_type == "memory" for n in only_mem)


def test_obsidian_list_notes_include_checkpoints_workspace_isolation(vault_dir):
    """R1 edge — scoped include_checkpoints must not leak another workspace's
    Checkpoints tree into the result set."""
    store = ObsidianMemoryStore(vault_dir)
    store.append_checkpoint(
        workspace_slug="blobert",
        ticket_id="feat-other-ws",
        run_id="run-other",
        entry=f"### Assumption\n{_CHECKPOINT_NEEDLE} other-ws",
    )
    store.append_checkpoint(
        workspace_slug="loregarden",
        ticket_id="feat-this-ws",
        run_id="run-this",
        entry=f"### Assumption\n{_CHECKPOINT_NEEDLE} this-ws",
    )

    scoped = store.list_notes(workspace_slug="loregarden", include_checkpoints=True)
    paths = [n.path for n in scoped if n.note_type == "checkpoint"]
    assert paths
    assert all("/loregarden/" in p.replace("\\", "/") for p in paths)
    assert not any("/blobert/" in p.replace("\\", "/") for p in paths)


def test_obsidian_search_pure_checkpoint_query_fills_limit(vault_dir):
    """R2 edge — when only checkpoints match, fill the limit with checkpoint
    hits (do not return [] just because non-checkpoint bucket is empty)."""
    store = ObsidianMemoryStore(vault_dir)
    needle = "pure-checkpoint-needle-718def"
    for index in range(5):
        store.append_checkpoint(
            workspace_slug="loregarden",
            ticket_id="feat-cp-pure",
            run_id=f"run-pure-{index}",
            entry=f"### Assumption\n{needle} entry {index}",
        )

    hits = store.search(needle, workspace_slug="loregarden", limit=3)
    assert len(hits) == 3
    assert all(hit.note_type == "checkpoint" for hit in hits)


def test_obsidian_search_file_level_hit_not_per_entry(vault_dir):
    """R2 mutation — multiple Assumption entries in one run log are still one
    file-level MemoryNote hit (entry split stays in absorb-adapt)."""
    store = ObsidianMemoryStore(vault_dir)
    needle = "file-level-needle-718ghi"
    store.append_checkpoint(
        workspace_slug="loregarden",
        ticket_id="feat-cp-entries",
        run_id="run-multi-entry",
        entry=f"### Assumption one\n{needle} first",
    )
    store.append_checkpoint(
        workspace_slug="loregarden",
        ticket_id="feat-cp-entries",
        run_id="run-multi-entry",
        entry=f"### Assumption two\n{needle} second",
    )

    hits = store.search(needle, workspace_slug="loregarden")
    assert len(hits) == 1
    assert hits[0].note_type == "checkpoint"
    assert needle in hits[0].body
    assert "first" in hits[0].body and "second" in hits[0].body


def test_obsidian_search_case_insensitive_checkpoint_match(vault_dir):
    """R2 edge — needle case must not hide a checkpoint body match (search
    already lowercases non-checkpoint haystacks)."""
    store = ObsidianMemoryStore(vault_dir)
    store.append_checkpoint(
        workspace_slug="loregarden",
        ticket_id="feat-cp-case",
        run_id="run-case",
        entry="### Assumption\nMiXeD-CaSe-Needle-718JKL was assumed.",
    )

    hits = store.search("mixed-case-needle-718jkl", workspace_slug="loregarden")
    assert len(hits) == 1
    assert hits[0].note_type == "checkpoint"


def test_obsidian_search_empty_and_whitespace_query_ignores_checkpoints(vault_dir):
    """Null/empty mutation — blank query returns [] even when checkpoints exist;
    must not dump the Checkpoints root."""
    store = ObsidianMemoryStore(vault_dir)
    store.append_checkpoint(
        workspace_slug="loregarden",
        ticket_id="feat-cp-blank",
        run_id="run-blank",
        entry=f"### Assumption\n{_CHECKPOINT_NEEDLE}",
    )

    assert store.search("", workspace_slug="loregarden") == []
    assert store.search("   ", workspace_slug="loregarden") == []


def test_obsidian_search_fill_order_includes_learnings_and_blog_before_checkpoints(
    vault_dir,
):
    """R2 combinatorial — non-checkpoint-first fill covers learnings and blog
    posts, not only memory notes."""
    store = ObsidianMemoryStore(vault_dir)
    needle = "mixed-types-needle-718mno"
    store.append_learning(
        ticket_id="feat-learn",
        workspace_slug="loregarden",
        content=f"{needle} in a learning",
    )
    store.upsert_blog_post(
        ticket_id="feat-blog",
        workspace_slug="loregarden",
        title="Blog hit",
        body=f"{needle} in a blog post",
    )
    store.append_checkpoint(
        workspace_slug="loregarden",
        ticket_id="feat-cp-mixed",
        run_id="run-mixed",
        entry=f"### Assumption\n{needle} in checkpoint",
    )

    hits = store.search(needle, workspace_slug="loregarden", limit=3)
    assert len(hits) == 3
    assert all(hit.note_type != "checkpoint" for hit in hits[:2])
    assert {hit.note_type for hit in hits[:2]} == {"learning", "blog_post"}
    assert hits[2].note_type == "checkpoint"


def test_obsidian_search_survives_memory_walk_budget_before_checkpoints(vault_dir):
    """R2 adversarial — a shared list_notes(limit=N) that walks Memory before
    Checkpoints and stops at N will never see a matching checkpoint once N
    non-matching memory notes exist. Search must still return the checkpoint.

    Uses N=500 to match the pre-change search enumeration budget.
    """
    store = ObsidianMemoryStore(vault_dir)
    needle = "budget-starve-needle-718pqr"
    for index in range(500):
        store.upsert_note(
            title=f"Filler {index}",
            body="unrelated filler body without the needle",
            workspace_slug="loregarden",
        )
    store.append_checkpoint(
        workspace_slug="loregarden",
        ticket_id="feat-cp-budget",
        run_id="run-budget",
        entry=f"### Assumption\n{needle} must remain findable",
    )

    hits = store.search(needle, workspace_slug="loregarden", limit=5)
    assert any(hit.note_type == "checkpoint" and "Checkpoints" in hit.path for hit in hits), (
        "matching checkpoint starved by non-matching memory walk budget"
    )


def test_obsidian_search_workspace_isolation_for_checkpoints(vault_dir):
    """R2 edge — search scoped to workspace A must not return workspace B's
    checkpoint even when the needle matches both."""
    store = ObsidianMemoryStore(vault_dir)
    needle = "ws-iso-needle-718stu"
    store.append_checkpoint(
        workspace_slug="blobert",
        ticket_id="feat-b",
        run_id="run-b",
        entry=f"### Assumption\n{needle}",
    )
    store.append_checkpoint(
        workspace_slug="loregarden",
        ticket_id="feat-a",
        run_id="run-a",
        entry=f"### Assumption\n{needle}",
    )

    hits = store.search(needle, workspace_slug="loregarden")
    assert len(hits) == 1
    assert hits[0].note_type == "checkpoint"
    assert "/loregarden/" in hits[0].path.replace("\\", "/")


def test_agent_memory_service_search_checkpoints_absent_from_graph(vault_dir, tmp_path):
    """R2/R6 — checkpoint hits stay Obsidian-only; graph array must stay empty
    for a checkpoint-only needle (no dual-write to SQLite)."""
    service = _both_backends(vault_dir, tmp_path)
    needle = "no-graph-needle-718vwx"
    service.append_checkpoint(
        ticket_id="feat-cp-nograph",
        workspace_slug="loregarden",
        run_id="run-nograph",
        entry=f"### Assumption\n{needle}",
    )

    found = service.search(needle, workspace_slug="loregarden")
    assert found["graph"] == []
    assert any(row["note_type"] == "checkpoint" for row in found["obsidian"])


def test_obsidian_search_non_checkpoint_fill_does_not_displace_for_checkpoints(vault_dir):
    """R2 inverse — when non-checkpoint matches already fill limit, checkpoints
    must not displace them (append-only after the non-cp bucket is full)."""
    store = ObsidianMemoryStore(vault_dir)
    needle = "no-displace-needle-718yz0"
    for index in range(4):
        store.upsert_note(
            title=f"Memory fill {index}",
            body=f"{needle} memory {index}",
            workspace_slug="loregarden",
        )
    for index in range(4):
        store.append_checkpoint(
            workspace_slug="loregarden",
            ticket_id="feat-cp-nodisp",
            run_id=f"run-nodisp-{index}",
            entry=f"### Assumption\n{needle} checkpoint {index}",
        )

    hits = store.search(needle, workspace_slug="loregarden", limit=3)
    assert len(hits) == 3
    assert all(hit.note_type != "checkpoint" for hit in hits)


def test_obsidian_search_unscoped_includes_checkpoints(vault_dir):
    """R2 edge — unscoped search (no workspace_slug) still returns checkpoint
    hits once include_checkpoints walks the Checkpoints root."""
    store = ObsidianMemoryStore(vault_dir)
    needle = "unscoped-cp-needle-718ab1"
    store.append_checkpoint(
        workspace_slug="loregarden",
        ticket_id="feat-cp-unscoped",
        run_id="run-unscoped",
        entry=f"### Assumption\n{needle}",
    )

    hits = store.search(needle)
    assert len(hits) == 1
    assert hits[0].note_type == "checkpoint"
    assert "Checkpoints" in hits[0].path


def test_recall_related_candidate_cap_unchanged_with_checkpoints_present(vault_dir, tmp_path):
    """R3 / Cutover R5 — a vault flooded with checkpoints must not be opened
    for durable recall; only GRAPH candidates rank."""
    service = _both_backends(vault_dir, tmp_path)
    for index in range(30):
        service.append_checkpoint(
            ticket_id=f"feat-flood-{index}",
            workspace_slug="loregarden",
            run_id=f"run-flood-{index}",
            entry=f"### Assumption\nflood entry {index} trusted server throttle",
        )
    service._graph_for_workspace("loregarden").upsert_node(
        title="Trusted server throttle",
        body="Cap the call rate.",
        workspace_slug="loregarden",
        node_type="memory",
    )

    with patch.object(
        service.obsidian, "list_notes", wraps=service.obsidian.list_notes
    ) as list_notes:
        ranked = service.recall_related("trusted server throttle", workspace_slug="loregarden")

    assert list_notes.call_count == 0
    assert any(row["title"] == "Trusted server throttle" for row in ranked)
    assert all(row["source"] == "sqlite" for row in ranked)
    assert not any(str(row.get("title", "")).startswith("Checkpoint log") for row in ranked)
