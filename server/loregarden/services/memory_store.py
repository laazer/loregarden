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
from loregarden.services.path_resolve import (
    is_under_icloud,
    resolve_icloud_root,
    sqlite_url_for_path,
)


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
        self._memory_subdir = settings.obsidian_memory_subdir
        self._learnings_subdir = settings.obsidian_learnings_subdir
        self._blogposts_subdir = settings.obsidian_blogposts_subdir
        self._checkpoints_subdir = settings.obsidian_checkpoints_subdir

    @classmethod
    def from_settings(cls) -> ObsidianMemoryStore | None:
        vault = resolved_obsidian_vault()
        if not vault:
            return None
        return cls(vault)

    def _workspace_segment(self, workspace_slug: str) -> str:
        return _slugify(workspace_slug.strip()) if workspace_slug.strip() else ""

    def memory_dir(self, workspace_slug: str = "") -> Path:
        base = self.vault_dir / self._memory_subdir
        segment = self._workspace_segment(workspace_slug)
        return base / segment if segment else base

    def learnings_dir(self, workspace_slug: str = "") -> Path:
        base = self.vault_dir / self._learnings_subdir
        segment = self._workspace_segment(workspace_slug)
        return base / segment if segment else base

    def blogposts_dir(self, workspace_slug: str = "") -> Path:
        base = self.vault_dir / self._blogposts_subdir
        segment = self._workspace_segment(workspace_slug)
        return base / segment if segment else base

    def checkpoints_dir(self, workspace_slug: str = "") -> Path:
        base = self.vault_dir / self._checkpoints_subdir
        segment = self._workspace_segment(workspace_slug)
        return base / segment if segment else base

    def _dir_for_note_type(self, note_type: str, workspace_slug: str = "") -> Path:
        if note_type == "learning":
            return self.learnings_dir(workspace_slug)
        if note_type == "blog_post":
            return self.blogposts_dir(workspace_slug)
        return self.memory_dir(workspace_slug)

    def _note_path(
        self,
        *,
        note_type: str,
        note_id: str,
        title: str,
        workspace_slug: str = "",
    ) -> Path:
        base = self._dir_for_note_type(note_type, workspace_slug)
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
        path = self._note_path(
            note_type=note_type,
            note_id=note_id,
            title=title,
            workspace_slug=workspace_slug,
        )
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

    def upsert_blog_post(
        self,
        *,
        ticket_id: str,
        workspace_slug: str,
        title: str,
        body: str,
        tags: list[str] | None = None,
        note_id: str = "",
    ) -> MemoryNote:
        merged_tags = ["blog_post", "loregarden", *(tags or [])]
        return self.upsert_note(
            note_id=note_id,
            title=title,
            body=body,
            tags=merged_tags,
            ticket_id=ticket_id,
            workspace_slug=workspace_slug,
            note_type="blog_post",
        )

    def append_checkpoint(
        self,
        *,
        workspace_slug: str,
        ticket_id: str,
        run_id: str,
        entry: str,
    ) -> dict[str, str]:
        """Append one checkpoint entry to this ticket+run's log file, creating
        it (with a frontmatter header) on first write. Unlike upsert_note's
        one-file-per-call notes, multiple entries accumulate in one file across
        a run — matching the checkpoint protocol's <ticket-id>/<run-id>.md log.
        """
        base = self.checkpoints_dir(workspace_slug)
        ticket_slug = _slugify(ticket_id) if ticket_id.strip() else "ticket"
        run_slug = _slugify(run_id) if run_id.strip() else "run"
        path = base / ticket_slug / f"{run_slug}.md"
        path.parent.mkdir(parents=True, exist_ok=True)

        if not path.is_file():
            header = _format_frontmatter(
                {
                    "type": "checkpoint",
                    "ticket_id": ticket_id,
                    "run_id": run_id,
                    "workspace": workspace_slug,
                    "created": _utcnow_iso(),
                }
            )
            path.write_text(
                f"{header}\n\n# Checkpoint log — {ticket_id} / {run_id}\n\n", encoding="utf-8"
            )

        with path.open("a", encoding="utf-8") as handle:
            handle.write(entry.strip() + "\n\n")

        return {
            "path": str(path.relative_to(self.vault_dir)),
            "ticket_id": ticket_id,
            "run_id": run_id,
        }

    def list_notes(
        self,
        *,
        note_type: str = "",
        workspace_slug: str = "",
        limit: int = 50,
    ) -> list[MemoryNote]:
        if workspace_slug.strip():
            roots = [
                self.memory_dir(workspace_slug),
                self.learnings_dir(workspace_slug),
                self.blogposts_dir(workspace_slug),
            ]
        else:
            memory_root = self.vault_dir / self._memory_subdir
            learnings_root = self.vault_dir / self._learnings_subdir
            blogposts_root = self.vault_dir / self._blogposts_subdir
            roots = [p for p in (memory_root, learnings_root, blogposts_root) if p.is_dir()]
        notes: list[MemoryNote] = []
        for root in roots:
            if not root.is_dir():
                continue
            for path in sorted(root.rglob("*.md"), key=lambda p: p.stat().st_mtime, reverse=True):
                note = self._read_note(path)
                if note and (not note_type or note.note_type == note_type):
                    if workspace_slug.strip() and note.workspace_slug != workspace_slug.strip():
                        continue
                    notes.append(note)
                if len(notes) >= limit:
                    return notes
        return notes

    def search(
        self,
        query: str,
        *,
        workspace_slug: str = "",
        limit: int = 20,
    ) -> list[MemoryNote]:
        needle = query.strip().lower()
        if not needle:
            return []
        hits: list[MemoryNote] = []
        for note in self.list_notes(workspace_slug=workspace_slug, limit=500):
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
        for line in fm_match.group(1).splitlines() if fm_match else []:
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
                CREATE INDEX IF NOT EXISTS ix_memory_nodes_workspace ON memory_nodes(workspace_slug);
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
            row = conn.execute(
                "SELECT created_at FROM memory_nodes WHERE id = ?", (node_id,)
            ).fetchone()
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

    def search(
        self,
        query: str,
        *,
        workspace_slug: str = "",
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        needle = f"%{query.strip()}%"
        if query.strip() == "":
            return []
        slug = workspace_slug.strip()
        with self._connect() as conn:
            if slug:
                rows = conn.execute(
                    """
                    SELECT id, title, body, tags_json, ticket_id, workspace_slug, node_type, created_at, updated_at
                    FROM memory_nodes
                    WHERE workspace_slug = ?
                      AND (title LIKE ? OR body LIKE ? OR tags_json LIKE ?)
                    ORDER BY updated_at DESC
                    LIMIT ?
                    """,
                    (slug, needle, needle, needle, limit),
                ).fetchall()
            else:
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
    """Facade — writes to Obsidian and/or per-workspace memory SQLite when configured."""

    def __init__(
        self,
        obsidian: ObsidianMemoryStore | None = None,
        graph_sqlite_base: Path | None = None,
    ) -> None:
        self.obsidian = obsidian
        self._graph_sqlite_base = graph_sqlite_base

    @classmethod
    def from_settings(cls) -> AgentMemoryService:
        return cls(
            obsidian=ObsidianMemoryStore.from_settings(),
            graph_sqlite_base=resolved_memory_sqlite_path(),
        )

    def _graph_path_for_workspace(self, workspace_slug: str) -> Path | None:
        base = self._graph_sqlite_base or resolved_memory_sqlite_path()
        if not base:
            return None
        slug = workspace_slug.strip()
        if not slug:
            return base
        return base.parent / slug / base.name

    def _graph_for_workspace(self, workspace_slug: str) -> MemoryGraphStore | None:
        path = self._graph_path_for_workspace(workspace_slug)
        if not path:
            return None
        return MemoryGraphStore(path)

    def status(self, *, workspace_slug: str = "") -> dict[str, Any]:
        obsidian_vault = resolved_obsidian_vault()
        memory_db = resolved_memory_sqlite_path(workspace_slug)
        slug = workspace_slug.strip()
        return {
            "enabled": self.obsidian is not None or memory_db is not None,
            "workspace_slug": slug or None,
            "obsidian_vault": str(obsidian_vault) if obsidian_vault else None,
            "obsidian_memory_dir": (
                str(self.obsidian.memory_dir(slug)) if self.obsidian and obsidian_vault else None
            ),
            "obsidian_learnings_dir": (
                str(self.obsidian.learnings_dir(slug)) if self.obsidian and obsidian_vault else None
            ),
            "obsidian_blogposts_dir": (
                str(self.obsidian.blogposts_dir(slug)) if self.obsidian and obsidian_vault else None
            ),
            "obsidian_checkpoints_dir": (
                str(self.obsidian.checkpoints_dir(slug))
                if self.obsidian and obsidian_vault
                else None
            ),
            "memory_sqlite_path": str(memory_db) if memory_db else None,
            "memory_sqlite_url": sqlite_url_for_path(memory_db) if memory_db else None,
            "memory_sqlite_in_icloud": (
                is_under_icloud(memory_db, resolve_icloud_root(settings.icloud_root))
                if memory_db
                else False
            ),
            "memory_graph_tables": ["memory_nodes", "memory_relations"] if memory_db else [],
            "memory_graph_node_types": ["memory", "learning"] if memory_db else [],
            "memory_graph_excludes": ["blog_post", "checkpoint"],
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
        graph = self._graph_for_workspace(workspace_slug)
        if not self.obsidian and not graph:
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
        if graph:
            node = graph.upsert_node(
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
        graph = self._graph_for_workspace(workspace_slug)
        if not self.obsidian and not graph:
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
        if graph:
            result["graph"] = graph.upsert_node(
                node_id=node_id,
                title=title,
                body=body,
                tags=tags,
                ticket_id=ticket_id,
                workspace_slug=workspace_slug,
                node_type="memory",
            )
        return result

    def upsert_blog_post(
        self,
        *,
        ticket_id: str,
        workspace_slug: str,
        title: str,
        body: str,
        tags: list[str] | None = None,
        note_id: str = "",
    ) -> dict[str, Any]:
        if not self.obsidian:
            raise ValueError(
                "No Obsidian vault configured. Set LOREGARDEN_OBSIDIAN_VAULT_DIR for blog post storage."
            )
        note = self.obsidian.upsert_blog_post(
            ticket_id=ticket_id,
            workspace_slug=workspace_slug,
            title=title,
            body=body,
            tags=tags,
            note_id=note_id,
        )
        return {
            "ticket_id": ticket_id,
            "workspace_slug": workspace_slug,
            "obsidian": {"id": note.id, "path": note.path, "updated_at": note.updated_at},
        }

    def append_checkpoint(
        self,
        *,
        ticket_id: str,
        workspace_slug: str,
        run_id: str,
        entry: str,
    ) -> dict[str, Any]:
        if not self.obsidian:
            raise ValueError(
                "No Obsidian vault configured. Set LOREGARDEN_OBSIDIAN_VAULT_DIR for checkpoint storage."
            )
        result = self.obsidian.append_checkpoint(
            workspace_slug=workspace_slug,
            ticket_id=ticket_id,
            run_id=run_id,
            entry=entry,
        )
        return {
            "ticket_id": ticket_id,
            "workspace_slug": workspace_slug,
            "run_id": run_id,
            "obsidian": {"path": result["path"]},
        }

    def create_relation(
        self,
        *,
        source_id: str,
        target_id: str,
        relation_type: str = "related",
        workspace_slug: str = "",
    ) -> dict[str, Any]:
        graph = self._graph_for_workspace(workspace_slug)
        if not graph:
            raise ValueError("Memory graph SQLite is not configured.")
        return graph.create_relation(
            source_id=source_id,
            target_id=target_id,
            relation_type=relation_type,
        )

    def search(
        self,
        query: str,
        *,
        workspace_slug: str = "",
        limit: int = 20,
    ) -> dict[str, Any]:
        obsidian_hits: list[dict[str, Any]] = []
        graph_hits: list[dict[str, Any]] = []
        graph = self._graph_for_workspace(workspace_slug)
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
                for n in self.obsidian.search(query, workspace_slug=workspace_slug, limit=limit)
            ]
        if graph:
            graph_hits = [
                {**row, "source": "sqlite"}
                for row in graph.search(query, workspace_slug=workspace_slug, limit=limit)
            ]
        return {
            "query": query,
            "workspace_slug": workspace_slug.strip() or None,
            "obsidian": obsidian_hits,
            "graph": graph_hits,
        }
