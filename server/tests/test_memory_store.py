import json
from pathlib import Path

import pytest
from loregarden.services.memory_store import (
    AgentMemoryService,
    MemoryGraphStore,
    ObsidianMemoryStore,
)


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
