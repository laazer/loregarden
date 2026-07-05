"""Agent memory — Obsidian markdown notes + optional SQLite graph in iCloud."""

from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from loregarden.config import (
    resolved_memory_sqlite_path,
    resolved_obsidian_vault,
    settings,
)
from loregarden.services.path_resolve import is_under_icloud, resolve_icloud_root, sqlite_url_for_path


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _slugify(text: str, *, max_len: int = 80) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", text.strip().lower()).strip("-")
    if not slug:
        slug = "note"
    return slug[:max_len].rstrip("-")


def _format_frontmatter(fields: dict[str, Any]) -> str:
    lines = ["---"]
    for key, value in fields.items():
        if value is None or value == "":
            continue
        if isinstance(value, list):
            lines.append(f"{key}:")
            for item in value:
                lines.append(f"  - {item}")
        elif isinstance(value, bool):
            lines.append(f"{key}: {'true' if value else 'false'}")
        else:
            escaped = str(value).replace('"', '\\"')
            lines.append(f'{key}: "{escaped}"')
    lines.append("---")
    return "\n".join(lines)


@dataclass
class MemoryNote:
    id: str
    path: str
    title: str
    body: str
    tags: list[str]
    ticket_id: str
    workspace_slug: str
    note_type: str
    created_at: str
    updated_at: str


class ObsidianMemoryStore:
    """Write human-readable memory notes into an Obsidian vault (typically synced via iCloud)."""

    def __init__(self, vault_dir: Path) -> None:
        self.vault_dir = vault_dir.resolve()
        self.memory_dir = self.vault_dir / settings.obsidian_memory_subdir
        self.learnings_dir = self.vault_dir / settings.obsidian_learnings_subdir

    @classmethod
    def from_settings(cls) -> ObsidianMemoryStore | None:
        vault = resolved_obsidian_vault()
        if not vault:
            return None
        return cls(vault)

    def _note_path(self, *, note_type: str, note_id: str, title: str) -> Path:
        base = self.learnings_dir if note_type == "learning" else self.memory_dir
        filename = f"{_slugify(title)}-{note_id[:8]}.md"
        return base / filename

    def upsert_note(
        self,
        *,
        note_id: str = "",
        title: str,
        body: str,
        tags: list[str] | None = None,
        ticket_id: str = "",
        workspace_slug: str = "",
        note_type: str = "memory",
    ) -> MemoryNote:
        note_id = note_id.strip() or str(uuid4())
        tags = list(tags or [])
        now = _utcnow_iso()
        path = self._note_path(note_type=note_type, note_id=note_id, title=title)
        path.parent.mkdir(parents=True, exist_ok=True)

        created_at = now
        if path.is_file():
            existing = path.read_text(encoding="utf-8")
            match = re.search(r'^created:\s*"([^"]+)"', existing, re.MULTILINE)
            if match:
                created_at = match.group(1)

        frontmatter = _format_frontmatter(
            {
                "id": note_id,
                "type": note_type,
                "title": title,
                "tags": tags,
                "ticket_id": ticket_id,
                "workspace": workspace_slug,
                "created": created_at,
                "updated": now,
            }
        )
        path.write_text(f"{frontmatter}\n\n# {title}\n\n{body.strip()}\n", encoding="utf-8")
        return MemoryNote(
            id=note_id,
            path=str(path.relative_to(self.vault_dir)),
            title=title,
            body=body,
            tags=tags,
            ticket_id=ticket_id,
            workspace_slug=workspace_slug,
            note_type=note_type,
            created_at=created_at,
            updated_at=now,
        )

    def append_learning(
        self,
        *,
        ticket_id: str,
        workspace_slug: str,
        content: str,
        tags: list[str] | None = None,
    ) -> MemoryNote:
        title = f"Learning — {ticket_id}"
        merged_tags = ["learning", "loregarden", *(tags or [])]
        return self.upsert_note(
            title=title,
            body=content,
            tags=merged_tags,
            ticket_id=ticket_id,
            workspace_slug=workspace_slug,
            note_type="learning",
        )

    def list_notes(self, *, note_type: str = "", limit: int = 50) -> list[MemoryNote]:
        roots = [self.memory_dir, self.learnings_dir]
        notes: list[MemoryNote] = []
        for root in roots:
            if not root.is_dir():
                continue
            for path in sorted(root.rglob("*.md"), key=lambda p: p.stat().st_mtime, reverse=True):
                note = self._read_note(path)
                if note and (not note_type or note.note_type == note_type):
                    notes.append(note)
                if len(notes) >= limit:
                    return notes
        return notes

    def search(self, query: str, *, limit: int = 20) -> list[MemoryNote]:
        needle = query.strip().lower()
        if not needle:
            return []
        hits: list[MemoryNote] = []
        for note in self.list_notes(limit=500):
            haystack = f"{note.title}\n{note.body}\n{' '.join(note.tags)}".lower()
            if needle in haystack:
                hits.append(note)
            if len(hits) >= limit:
                break
        return hits

    def _read_note(self, path: Path) -> MemoryNote | None:
        if not path.is_file():
            return None
        text = path.read_text(encoding="utf-8")
        fm_match = re.match(r"^---\n(.*?)\n---\n", text, re.DOTALL)
        body = text
        fields: dict[str, str] = {}
        if fm_match:
            body = text[fm_match.end() :].strip()
            for line in fm_match.group(1).splitlines():
                if ":" not in line or line.strip().startswith("- "):
                    continue
                key, value = line.split(":", 1)
                fields[key.strip()] = value.strip().strip('"')

        title_match = re.search(r"^#\s+(.+)$", body, re.MULTILINE)
        title = title_match.group(1).strip() if title_match else path.stem
        tags: list[str] = []
        for line in (fm_match.group(1).splitlines() if fm_match else []):
            stripped = line.strip()
            if stripped.startswith("- "):
                tags.append(stripped[2:].strip())

        return MemoryNote(
            id=fields.get("id", path.stem),
            path=str(path.relative_to(self.vault_dir)),
            title=title,
            body=body,
            tags=tags,
            ticket_id=fields.get("ticket_id", ""),
            workspace_slug=fields.get("workspace", ""),
            note_type=fields.get("type", "memory"),
            created_at=fields.get("created", ""),
            updated_at=fields.get("updated", ""),
        )


class MemoryGraphStore:
    """Structured memory graph backed by SQLite (safe for iCloud when using DELETE journal)."""

    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path.resolve()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._icloud_root = resolve_icloud_root(settings.icloud_root)
        self._init_schema()

    @classmethod
    def from_settings(cls) -> MemoryGraphStore | None:
        path = resolved_memory_sqlite_path()
        if not path:
            return None
        return cls(path)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=30.0)
        conn.row_factory = sqlite3.Row
        if is_under_icloud(self.db_path, self._icloud_root):
            conn.execute("PRAGMA journal_mode=DELETE")
            conn.execute("PRAGMA synchronous=FULL")
        else:
            conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=30000")
        return conn

    def _init_schema(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS memory_nodes (
                    id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    body TEXT NOT NULL DEFAULT '',
                    tags_json TEXT NOT NULL DEFAULT '[]',
                    ticket_id TEXT NOT NULL DEFAULT '',
                    workspace_slug TEXT NOT NULL DEFAULT '',
                    node_type TEXT NOT NULL DEFAULT 'memory',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS memory_relations (
                    id TEXT PRIMARY KEY,
                    source_id TEXT NOT NULL,
                    target_id TEXT NOT NULL,
                    relation_type TEXT NOT NULL DEFAULT 'related',
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(source_id) REFERENCES memory_nodes(id),
                    FOREIGN KEY(target_id) REFERENCES memory_nodes(id)
                );
                CREATE INDEX IF NOT EXISTS ix_memory_nodes_ticket ON memory_nodes(ticket_id);
                CREATE INDEX IF NOT EXISTS ix_memory_relations_source ON memory_relations(source_id);
                """
            )

    def upsert_node(
        self,
        *,
        node_id: str = "",
        title: str,
        body: str = "",
        tags: list[str] | None = None,
        ticket_id: str = "",
        workspace_slug: str = "",
        node_type: str = "memory",
    ) -> dict[str, Any]:
        node_id = node_id.strip() or str(uuid4())
        now = _utcnow_iso()
        tags_json = json.dumps(tags or [])
        with self._connect() as conn:
            row = conn.execute("SELECT created_at FROM memory_nodes WHERE id = ?", (node_id,)).fetchone()
            created_at = row["created_at"] if row else now
            conn.execute(
                """
                INSERT INTO memory_nodes (
                    id, title, body, tags_json, ticket_id, workspace_slug, node_type, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    title = excluded.title,
                    body = excluded.body,
                    tags_json = excluded.tags_json,
                    ticket_id = excluded.ticket_id,
                    workspace_slug = excluded.workspace_slug,
                    node_type = excluded.node_type,
                    updated_at = excluded.updated_at
                """,
                (
                    node_id,
                    title,
                    body,
                    tags_json,
                    ticket_id,
                    workspace_slug,
                    node_type,
                    created_at,
                    now,
                ),
            )
        return {
            "id": node_id,
            "title": title,
            "body": body,
            "tags": tags or [],
            "ticket_id": ticket_id,
            "workspace_slug": workspace_slug,
            "node_type": node_type,
            "created_at": created_at,
            "updated_at": now,
            "sqlite_path": str(self.db_path),
        }

    def create_relation(
        self,
        *,
        source_id: str,
        target_id: str,
        relation_type: str = "related",
    ) -> dict[str, Any]:
        relation_id = str(uuid4())
        now = _utcnow_iso()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO memory_relations (id, source_id, target_id, relation_type, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (relation_id, source_id, target_id, relation_type, now),
            )
        return {
            "id": relation_id,
            "source_id": source_id,
            "target_id": target_id,
            "relation_type": relation_type,
            "created_at": now,
        }

    def search(self, query: str, *, limit: int = 20) -> list[dict[str, Any]]:
        needle = f"%{query.strip()}%"
        if query.strip() == "":
            return []
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT id, title, body, tags_json, ticket_id, workspace_slug, node_type, created_at, updated_at
                FROM memory_nodes
                WHERE title LIKE ? OR body LIKE ? OR tags_json LIKE ?
                ORDER BY updated_at DESC
                LIMIT ?
                """,
                (needle, needle, needle, limit),
            ).fetchall()
        return [
            {
                "id": row["id"],
                "title": row["title"],
                "body": row["body"],
                "tags": json.loads(row["tags_json"] or "[]"),
                "ticket_id": row["ticket_id"],
                "workspace_slug": row["workspace_slug"],
                "node_type": row["node_type"],
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
            }
            for row in rows
        ]

    def sqlite_url(self) -> str:
        return sqlite_url_for_path(self.db_path)


class AgentMemoryService:
    """Facade — writes to Obsidian and/or memory SQLite when configured."""

    def __init__(
        self,
        obsidian: ObsidianMemoryStore | None = None,
        graph: MemoryGraphStore | None = None,
    ) -> None:
        self.obsidian = obsidian
        self.graph = graph

    @classmethod
    def from_settings(cls) -> AgentMemoryService:
        return cls(
            obsidian=ObsidianMemoryStore.from_settings(),
            graph=MemoryGraphStore.from_settings(),
        )

    def status(self) -> dict[str, Any]:
        obsidian_vault = resolved_obsidian_vault()
        memory_db = resolved_memory_sqlite_path()
        return {
            "enabled": self.obsidian is not None or self.graph is not None,
            "obsidian_vault": str(obsidian_vault) if obsidian_vault else None,
            "obsidian_memory_dir": (
                str(obsidian_vault / settings.obsidian_memory_subdir) if obsidian_vault else None
            ),
            "obsidian_learnings_dir": (
                str(obsidian_vault / settings.obsidian_learnings_subdir) if obsidian_vault else None
            ),
            "memory_sqlite_path": str(memory_db) if memory_db else None,
            "memory_sqlite_in_icloud": (
                is_under_icloud(memory_db, resolve_icloud_root(settings.icloud_root))
                if memory_db
                else False
            ),
            "database_path": str(settings.database_url),
        }

    def append_learning(
        self,
        *,
        ticket_id: str,
        workspace_slug: str,
        content: str,
        tags: list[str] | None = None,
    ) -> dict[str, Any]:
        if not self.obsidian and not self.graph:
            raise ValueError(
                "No memory backend configured. Set LOREGARDEN_OBSIDIAN_VAULT_DIR and/or "
                "LOREGARDEN_MEMORY_SQLITE_URL (or enable iCloud defaults)."
            )
        result: dict[str, Any] = {"ticket_id": ticket_id, "workspace_slug": workspace_slug}
        if self.obsidian:
            note = self.obsidian.append_learning(
                ticket_id=ticket_id,
                workspace_slug=workspace_slug,
                content=content,
                tags=tags,
            )
            result["obsidian"] = {
                "id": note.id,
                "path": note.path,
                "updated_at": note.updated_at,
            }
        if self.graph:
            node = self.graph.upsert_node(
                title=f"Learning — {ticket_id}",
                body=content,
                tags=["learning", "loregarden", *(tags or [])],
                ticket_id=ticket_id,
                workspace_slug=workspace_slug,
                node_type="learning",
            )
            result["graph"] = node
        return result

    def upsert_memory(
        self,
        *,
        node_id: str = "",
        title: str,
        body: str = "",
        tags: list[str] | None = None,
        ticket_id: str = "",
        workspace_slug: str = "",
    ) -> dict[str, Any]:
        if not self.obsidian and not self.graph:
            raise ValueError("No memory backend configured.")
        result: dict[str, Any] = {}
        if self.obsidian:
            note = self.obsidian.upsert_note(
                note_id=node_id,
                title=title,
                body=body,
                tags=tags,
                ticket_id=ticket_id,
                workspace_slug=workspace_slug,
                note_type="memory",
            )
            result["obsidian"] = {"id": note.id, "path": note.path, "updated_at": note.updated_at}
        if self.graph:
            result["graph"] = self.graph.upsert_node(
                node_id=node_id,
                title=title,
                body=body,
                tags=tags,
                ticket_id=ticket_id,
                workspace_slug=workspace_slug,
                node_type="memory",
            )
        return result

    def create_relation(
        self,
        *,
        source_id: str,
        target_id: str,
        relation_type: str = "related",
    ) -> dict[str, Any]:
        if not self.graph:
            raise ValueError("Memory graph SQLite is not configured.")
        return self.graph.create_relation(
            source_id=source_id,
            target_id=target_id,
            relation_type=relation_type,
        )

    def search(self, query: str, *, limit: int = 20) -> dict[str, Any]:
        obsidian_hits: list[dict[str, Any]] = []
        graph_hits: list[dict[str, Any]] = []
        if self.obsidian:
            obsidian_hits = [
                {
                    "id": n.id,
                    "title": n.title,
                    "body": n.body,
                    "tags": n.tags,
                    "ticket_id": n.ticket_id,
                    "workspace_slug": n.workspace_slug,
                    "note_type": n.note_type,
                    "path": n.path,
                    "source": "obsidian",
                }
                for n in self.obsidian.search(query, limit=limit)
            ]
        if self.graph:
            graph_hits = [{**row, "source": "sqlite"} for row in self.graph.search(query, limit=limit)]
        return {"query": query, "obsidian": obsidian_hits, "graph": graph_hits}
