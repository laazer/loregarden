import json
from pathlib import Path
from unittest.mock import patch

import pytest
from loregarden.services.memory_store import (
    RECALL_CANDIDATE_CAP,
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
    rel = graph.create_relation(source_id=a["id"], target_id=b["id"], relation_type="supports")
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


def test_agent_memory_service_dual_write(vault_dir, tmp_path):
    service = AgentMemoryService(
        obsidian=ObsidianMemoryStore(vault_dir),
        graph_sqlite_base=tmp_path / "Loregarden" / "memory.db",
    )
    result = service.append_learning(
        ticket_id="t-01",
        workspace_slug="loregarden",
        content="Dual-write learning test.",
    )
    assert "obsidian" in result
    assert "graph" in result
    assert "loregarden" in result["obsidian"]["path"]
    search = service.search("dual-write", workspace_slug="loregarden")
    assert len(search["obsidian"]) == 1
    assert len(search["graph"]) == 1
    other_search = service.search("dual-write", workspace_slug="other")
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

        search = json.loads(
            execute_tool(
                session,
                "loregarden_search_memory",
                {"query": "checkpoint", "workspace_slug": "loregarden"},
            )
        )
        assert len(search["obsidian"]) >= 1
        assert len(search["graph"]) >= 1

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
# R3 — AgentMemoryService.recall_related: the both-stores term-overlap read.
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


def test_recall_related_reads_both_stores(vault_dir, tmp_path):
    """AC3.2 — the graph half holds 285 live nodes. Catches an implementation
    that ranks the Obsidian half only, which passes every test copied from the
    existing graph_sqlite_base=None fixtures."""
    service = _both_backends(vault_dir, tmp_path)
    service.obsidian.upsert_note(
        title="Trusted server throttle",
        body="Cap the call rate per tool.",
        workspace_slug="lg",
    )
    graph = service._graph_for_workspace("lg")
    graph.upsert_node(
        title="Retry budget",
        body="A throttled server returns before the trusted retry loop runs.",
        workspace_slug="lg",
    )

    titles = {row["title"] for row in service.recall_related("trusted server", workspace_slug="lg")}
    assert titles == {"Trusted server throttle", "Retry budget"}


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


def test_recall_related_works_with_obsidian_alone(vault_dir):
    """AC3.3 — the mirror case, and the shape every existing inherited-wisdom
    test uses."""
    service = AgentMemoryService(obsidian=ObsidianMemoryStore(vault_dir), graph_sqlite_base=None)
    service.obsidian.upsert_note(
        title="Trusted server throttle",
        body="Cap the call rate per tool.",
        workspace_slug="lg",
    )

    ranked = service.recall_related("trusted server", workspace_slug="lg")
    assert [row["title"] for row in ranked] == ["Trusted server throttle"]


def test_recall_related_deduplicates_a_dual_written_learning(vault_dir, tmp_path):
    """AC3.4 — append_learning writes the same title+body to both stores under
    two different uuid4s, so a dedupe keyed on id sees two records and burns two
    of the five briefing slots on one learning."""
    service = _both_backends(vault_dir, tmp_path)
    service.append_learning(
        ticket_id="t-01",
        workspace_slug="lg",
        content="Throttle the trusted server before the retry loop consumes the budget.",
    )

    ranked = service.recall_related("trusted server throttle", workspace_slug="lg")
    assert [row["title"] for row in ranked] == ["Learning — t-01"]


def test_recall_related_ranks_across_the_two_stores_together(vault_dir, tmp_path):
    """AC3.5 — catches an implementation that concatenates per-store ranked
    lists instead of ranking the union: there the Obsidian note always leads,
    whatever it scored."""
    service = _both_backends(vault_dir, tmp_path)
    service.obsidian.upsert_note(
        title="Weekly notes",
        body="A throttled endpoint came up.",
        workspace_slug="lg",
    )
    service._graph_for_workspace("lg").upsert_node(
        title="Retry budget",
        body="A throttled server returns before the trusted retry loop runs.",
        workspace_slug="lg",
    )

    ranked = service.recall_related("trusted server throttled", workspace_slug="lg")
    assert [row["title"] for row in ranked] == ["Retry budget", "Weekly notes"]


def test_recall_related_ranks_a_newer_note_above_an_equally_matching_older_one(vault_dir, tmp_path):
    """AC3.5 / AC2 — the recency tiebreak, verified across the two stores."""
    service = _both_backends(vault_dir, tmp_path)
    with frozen_clock("2026-01-01T00:00:00+00:00"):
        service.obsidian.upsert_note(
            title="Older throttle note", body="trusted server", workspace_slug="lg"
        )
    with frozen_clock("2026-02-01T00:00:00+00:00"):
        service._graph_for_workspace("lg").upsert_node(
            title="Newer throttle node", body="trusted server", workspace_slug="lg"
        )

    ranked = service.recall_related("trusted server", workspace_slug="lg")
    assert [row["title"] for row in ranked] == ["Newer throttle node", "Older throttle note"]


def test_recall_related_truncates_to_its_limit_keeping_the_top_ranked(vault_dir, tmp_path):
    """AC3.6 / S6 — the `limit` parameter is otherwise never exercised: every
    other fixture in this change yields at most two candidates and every call
    site takes the default, so an implementation that ignores `limit` entirely
    passes the whole suite. End to end it is hidden too, because _memory_hits
    stops at its own _MAX_MEMORY_HITS.

    Three candidates with overlaps 3, 2 and 1 make the assertion independent of
    the recency tiebreak: truncation must drop the WEAKEST, not the last one
    enumerated, so a limit applied before ranking fails here as well."""
    service = _both_backends(vault_dir, tmp_path)
    service.obsidian.upsert_note(title="Weakest throttle note", body="Body.", workspace_slug="lg")
    service.obsidian.upsert_note(title="Trusted server", body="Body.", workspace_slug="lg")
    service.obsidian.upsert_note(title="Trusted server throttle", body="Body.", workspace_slug="lg")

    unlimited = service.recall_related("trusted server throttle", workspace_slug="lg")
    assert [row["title"] for row in unlimited] == [
        "Trusted server throttle",
        "Trusted server",
        "Weakest throttle note",
    ]

    ranked = service.recall_related("trusted server throttle", workspace_slug="lg", limit=2)
    assert [row["title"] for row in ranked] == ["Trusted server throttle", "Trusted server"]


def test_recall_related_makes_exactly_one_obsidian_pass_of_five_hundred(vault_dir, tmp_path):
    """AC3.6 / AC5 — the cost proof, and the only assertion that can make it. A
    results assertion passes whether the vault was read once or once per note,
    so AC5 is a call-count criterion by construction."""
    service = _both_backends(vault_dir, tmp_path)
    service.obsidian.upsert_note(
        title="Trusted server throttle", body="Cap the rate.", workspace_slug="lg"
    )

    with patch.object(
        service.obsidian, "list_notes", wraps=service.obsidian.list_notes
    ) as list_notes:
        service.recall_related("trusted server", workspace_slug="lg")

    assert list_notes.call_count == 1
    assert list_notes.call_args.kwargs["limit"] == RECALL_CANDIDATE_CAP


def test_recall_related_never_pre_filters_through_search(vault_dir, tmp_path):
    """AC3.7 — the bug itself. A recall_related that calls search() first ranks
    whatever survived the whole-query substring match, i.e. almost always
    nothing. Neither store's search may be touched."""
    service = _both_backends(vault_dir, tmp_path)
    service.obsidian.upsert_note(
        title="Trusted server throttle",
        body="Cap the call rate per tool.",
        workspace_slug="lg",
    )
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
    assert len(ranked) == 2


def test_service_search_still_substring_matches_and_keeps_its_envelope(vault_dir, tmp_path):
    """AC3.8 — guard. search() stays the loregarden_search_memory tool path;
    its envelope keys are read by mcp/tools.py and api/memory.py."""
    service = _both_backends(vault_dir, tmp_path)
    service.obsidian.upsert_note(
        title="Trusted server throttle", body="Cap the call rate.", workspace_slug="lg"
    )

    found = service.search("Cap the call", workspace_slug="lg")
    assert set(found) == {"query", "workspace_slug", "obsidian", "graph"}
    assert [row["title"] for row in found["obsidian"]] == ["Trusted server throttle"]
    assert service.search("throttle rate", workspace_slug="lg")["obsidian"] == []
