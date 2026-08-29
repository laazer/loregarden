"""Soft-invalidation: a discredited node stays on disk and is dropped from every read."""

from __future__ import annotations

import json
import sqlite3

import pytest
from loregarden.mcp.tool_ids import AUTO_APPROVED_MCP_TOOLS, McpTool
from loregarden.mcp.tools import execute_tool
from loregarden.services.memory_store import (
    AgentMemoryService,
    MemoryGraphStore,
    ObsidianMemoryStore,
)
from sqlmodel import Session


@pytest.fixture
def vault_dir(tmp_path):
    vault = tmp_path / "vault"
    vault.mkdir()
    return vault


def test_graph_search_omits_discredited_nodes(tmp_path):
    graph = MemoryGraphStore(tmp_path / "memory.db")
    live = graph.upsert_node(title="Keep this", body="valid fact", workspace_slug="lg")
    bad = graph.upsert_node(
        title="Hallucinated fact",
        body="wrong",
        workspace_slug="lg",
        discredited=True,
    )

    hits = graph.search("fact", workspace_slug="lg")
    assert [row["title"] for row in hits] == ["Keep this"]
    assert live["id"] not in {bad["id"]}


def test_graph_list_nodes_omits_discredited_nodes(tmp_path):
    graph = MemoryGraphStore(tmp_path / "memory.db")
    graph.upsert_node(title="Keep this", body="valid", workspace_slug="lg")
    graph.upsert_node(
        title="Hallucinated fact", body="wrong", workspace_slug="lg", discredited=True
    )

    titles = [row["title"] for row in graph.list_nodes(workspace_slug="lg")]
    assert titles == ["Keep this"]


def test_obsidian_search_and_list_notes_omit_discredited(vault_dir):
    store = ObsidianMemoryStore(vault_dir)
    store.upsert_note(
        title="Keep this",
        body="valid fact",
        workspace_slug="lg",
    )
    store.upsert_note(
        title="Hallucinated fact",
        body="wrong",
        workspace_slug="lg",
        discredited=True,
    )

    listed = store.list_notes(workspace_slug="lg")
    assert [note.title for note in listed] == ["Keep this"]
    hits = store.search("fact", workspace_slug="lg")
    assert [note.title for note in hits] == ["Keep this"]


def test_discrediting_deletes_nothing(vault_dir, tmp_path):
    service = AgentMemoryService(
        obsidian=ObsidianMemoryStore(vault_dir),
        graph_sqlite_base=tmp_path / "memory.db",
    )
    created = service.upsert_memory(
        title="Hallucinated fact",
        body="wrong",
        workspace_slug="lg",
    )
    node_id = created["graph"]["id"]
    note_path = vault_dir / created["obsidian"]["path"]

    service.upsert_memory(
        node_id=node_id,
        title="Hallucinated fact",
        body="wrong",
        workspace_slug="lg",
        discredited=True,
    )

    assert note_path.is_file()
    text = note_path.read_text(encoding="utf-8")
    assert "discredited: true" in text
    assert "Hallucinated fact" in text

    graph = MemoryGraphStore(tmp_path / "lg" / "memory.db")
    with graph._connect() as conn:
        row = conn.execute(
            "SELECT id, title, discredited FROM memory_nodes WHERE id = ?",
            (node_id,),
        ).fetchone()
    assert row is not None
    assert row["title"] == "Hallucinated fact"
    assert row["discredited"] == 1


def test_undiscredit_restores_the_read_paths(vault_dir, tmp_path):
    service = AgentMemoryService(
        obsidian=ObsidianMemoryStore(vault_dir),
        graph_sqlite_base=tmp_path / "memory.db",
    )
    created = service.upsert_memory(
        title="Recoverable fact",
        body="was wrong, now right",
        workspace_slug="lg",
    )
    node_id = created["graph"]["id"]
    service.upsert_memory(
        node_id=node_id,
        title="Recoverable fact",
        body="was wrong, now right",
        workspace_slug="lg",
        discredited=True,
    )
    assert service.search("Recoverable", workspace_slug="lg")["graph"] == []
    assert service.search("Recoverable", workspace_slug="lg")["obsidian"] == []

    service.upsert_memory(
        node_id=node_id,
        title="Recoverable fact",
        body="was wrong, now right",
        workspace_slug="lg",
        discredited=False,
    )
    graph_hits = service.search("Recoverable", workspace_slug="lg")["graph"]
    obsidian_hits = service.search("Recoverable", workspace_slug="lg")["obsidian"]
    assert [row["title"] for row in graph_hits] == ["Recoverable fact"]
    assert [row["title"] for row in obsidian_hits] == ["Recoverable fact"]


def test_existing_sqlite_shard_gains_the_column_without_losing_rows(tmp_path):
    db_path = tmp_path / "memory.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE memory_nodes (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                body TEXT NOT NULL DEFAULT '',
                tags_json TEXT NOT NULL DEFAULT '[]',
                ticket_id TEXT NOT NULL DEFAULT '',
                workspace_slug TEXT NOT NULL DEFAULT '',
                node_type TEXT NOT NULL DEFAULT 'memory',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            INSERT INTO memory_nodes (
                id, title, body, tags_json, ticket_id, workspace_slug,
                node_type, created_at, updated_at
            ) VALUES (?, ?, ?, '[]', '', 'lg', 'memory', 't', 't')
            """,
            ("old-id", "Old fact", "legacy body"),
        )
        conn.commit()

    graph = MemoryGraphStore(db_path)
    hits = graph.search("Old fact", workspace_slug="lg")
    assert [row["id"] for row in hits] == ["old-id"]
    with graph._connect() as conn:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(memory_nodes)")}
        flag = conn.execute("SELECT discredited FROM memory_nodes WHERE id = 'old-id'").fetchone()[
            0
        ]
    assert "discredited" in columns
    assert flag == 0


def test_discredited_related_target_is_not_reachable_via_current_readers(tmp_path):
    """179's 1-hop reader is not in yet. Pin the invariant it has to inherit:
    a discredited node that a live relation still points at must not come back
    from any node reader, so a join that uses those readers cannot resurface it.
    """
    graph = MemoryGraphStore(tmp_path / "memory.db")
    live = graph.upsert_node(title="Anchor", body="live", workspace_slug="lg")
    bad = graph.upsert_node(
        title="Discredited neighbor",
        body="wrong",
        workspace_slug="lg",
        discredited=True,
    )
    graph.create_relation(source_id=live["id"], target_id=bad["id"])

    assert graph.search("Discredited neighbor", workspace_slug="lg") == []
    assert bad["id"] not in {row["id"] for row in graph.list_nodes(workspace_slug="lg")}
    with graph._connect() as conn:
        rel = conn.execute(
            "SELECT target_id FROM memory_relations WHERE source_id = ?",
            (live["id"],),
        ).fetchone()
    assert rel["target_id"] == bad["id"]


def test_mcp_upsert_discredited_is_auto_approved_and_filters_search(
    client, vault_dir, tmp_path, monkeypatch
):
    monkeypatch.setattr("loregarden.config.settings.obsidian_vault_dir", str(vault_dir))
    monkeypatch.setattr(
        "loregarden.config.settings.memory_sqlite_url",
        f"sqlite:///{tmp_path / 'mcp-memory.db'}",
    )
    assert McpTool.UPSERT_MEMORY in AUTO_APPROVED_MCP_TOOLS

    from loregarden.db.session import engine

    with Session(engine) as session:
        created = json.loads(
            execute_tool(
                session,
                "loregarden_upsert_memory",
                {
                    "title": "Hallucinated protocol",
                    "body": "never do this",
                    "workspace_slug": "loregarden",
                },
            )
        )
        node_id = created["graph"]["id"]
        execute_tool(
            session,
            "loregarden_upsert_memory",
            {
                "node_id": node_id,
                "title": "Hallucinated protocol",
                "body": "never do this",
                "workspace_slug": "loregarden",
                "discredited": True,
            },
        )
        search = json.loads(
            execute_tool(
                session,
                "loregarden_search_memory",
                {"query": "Hallucinated", "workspace_slug": "loregarden"},
            )
        )
    assert search["graph"] == []
    assert search["obsidian"] == []
