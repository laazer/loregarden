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
from loregarden.models.domain.enums import MemoryStoreKind, MemoryStoreState, RelationDirection
from loregarden.services import term_overlap
from loregarden.services.memory_authority import SEARCH_GRAPH_KINDS, SEARCH_OBSIDIAN_KINDS
from loregarden.services.memory_export import export_node_to_vault
from loregarden.services.memory_history import (
    ChangeAttribution,
    NodeContent,
    ensure_history_schema,
    list_versions,
    record_superseded,
)
from loregarden.services.path_resolve import (
    is_under_icloud,
    resolve_icloud_root,
    sqlite_url_for_path,
)

# How many records `recall_related` may consider, per store, per prompt build.
# AC5 pins the Obsidian side at one pass over at most this many notes, and the
# graph side mirrors it so it cannot become the new cost centre. Cited by name
# in `list_nodes` and `recall_related`; there is no second literal.
RECALL_CANDIDATE_CAP = 500


#: Introduces each checkpoint entry inside a ticket+run log.
#:
#: Leading rather than trailing, which is what makes a log written across the
#: change readable: everything before the first marker is undelimited legacy
#: text and everything after one is exactly one entry. A terminator cannot say
#: that — the text before the first terminator is either a legacy prefix or the
#: first entry, with nothing to tell them apart.
#:
#: The boundary used to be a blank line, which is not a boundary: the checkpoint
#: protocol's own entry template is four fields, and an agent that writes them
#: with blank lines between — rather than the `\n`-joined form the template shows
#: — had its one checkpoint read back as four. Measured across the vault, 198 of
#: the 1662 entries the stage briefing injected were fragments of a split entry
#: carrying no information: a bare `### [id] Stage — label`, or `**Confidence:**
#: high` on its own. Each one also spent a slot of `_MAX_CHECKPOINTS`, evicting a
#: real prior decision, and was counted in `memory_briefings.checkpoints_injected`
#: as though continuity had worked.
#:
#: An HTML comment because the vault is read by humans in Obsidian, where it is
#: invisible: the marker must be something no author would write and no renderer
#: would show. `---` was the other candidate and is unusable — it is a thematic
#: break an entry may legitimately contain, and at the top of a file it is
#: frontmatter.
CHECKPOINT_ENTRY_DELIMITER = "<!-- checkpoint-entry -->"


class MemoryStoreReadError(Exception):
    """A store read failed, labelled with the store that failed it.

    Raised at the read itself rather than derived by a caller: by the time an
    exception has crossed `recall_related`, "the vault would not open" and "the
    graph would not open" look identical, and a briefing that cannot name the
    failing store sends an operator at the wrong system.
    """

    def __init__(self, store: MemoryStoreKind) -> None:
        super().__init__(f"{store.value} read failed")
        self.store = store


class MemoryNodeNotFoundError(LookupError):
    """No graph node has this id in this workspace's shard."""


class MemoryWriteNotAppliedError(RuntimeError):
    """A write returned but the row read back does not carry it.

    Raised rather than returning the unchanged row: a 200 describing a flag
    that was never set is the failure an operator can least afford to miss.
    """


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def slugify(text: str, *, max_len: int = 80) -> str:
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
    discredited: bool = False


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
        return slugify(workspace_slug.strip()) if workspace_slug.strip() else ""

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
        filename = f"{slugify(title)}-{note_id[:8]}.md"
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
        discredited: bool | None = None,
        derived: bool = False,
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
        existing_discredited = False
        if path.is_file():
            existing = self._read_note(path)
            if existing:
                created_at = existing.created_at or now
                existing_discredited = existing.discredited
        flag = existing_discredited if discredited is None else discredited

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
                "discredited": True if flag else None,
                "derived": True if derived else None,
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
            discredited=flag,
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

        Each entry is introduced by `CHECKPOINT_ENTRY_DELIMITER`, so a
        multi-paragraph entry survives the read back as one entry.
        """
        if CHECKPOINT_ENTRY_DELIMITER in entry:
            # Loud rather than lenient, per `reject_truncated_call`: an entry
            # carrying the marker would split itself on read, and the halves
            # would look exactly like two checkpoints someone meant to write.
            raise ValueError(
                f"checkpoint entry contains {CHECKPOINT_ENTRY_DELIMITER!r}, which is "
                "reserved as the entry separator. Remove it and re-send the entry."
            )
        base = self.checkpoints_dir(workspace_slug)
        ticket_slug = slugify(ticket_id) if ticket_id.strip() else "ticket"
        run_slug = slugify(run_id) if run_id.strip() else "run"
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
            handle.write(f"{CHECKPOINT_ENTRY_DELIMITER}\n\n{entry.strip()}\n\n")

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
        include_checkpoints: bool = False,
    ) -> list[MemoryNote]:
        # Checkpoint-only filter: walk Checkpoints alone so a shared limit with
        # Memory/Learnings/BlogPosts cannot starve the root search needs.
        if include_checkpoints and note_type == "checkpoint":
            roots = self._checkpoint_roots(workspace_slug)
        elif note_type == "checkpoint":
            roots = []
        else:
            roots = self._standard_note_roots(
                workspace_slug, include_checkpoints=include_checkpoints
            )
        notes: list[MemoryNote] = []
        for path in self._iter_note_paths(roots):
            note = self._read_note(path)
            if note and self._note_visible(
                note, note_type=note_type, workspace_slug=workspace_slug
            ):
                notes.append(note)
            if len(notes) >= limit:
                return notes
        return notes

    @staticmethod
    def _iter_note_paths(roots: list[Path]):
        for root in roots:
            if not root.is_dir():
                continue
            for path in sorted(root.rglob("*.md"), key=lambda p: p.stat().st_mtime, reverse=True):
                if "_orphans" not in path.parts:
                    yield path

    @staticmethod
    def _note_visible(
        note: MemoryNote,
        *,
        note_type: str,  # py-org: allow-string — open filter; "" means any note type
        workspace_slug: str,
    ) -> bool:
        if note_type and note.note_type != note_type:
            return False
        if workspace_slug.strip() and note.workspace_slug != workspace_slug.strip():
            return False
        return not note.discredited

    def _standard_note_roots(self, workspace_slug: str, *, include_checkpoints: bool) -> list[Path]:
        if workspace_slug.strip():
            roots = [
                self.memory_dir(workspace_slug),
                self.learnings_dir(workspace_slug),
                self.blogposts_dir(workspace_slug),
            ]
            if include_checkpoints:
                roots.extend(self._checkpoint_roots(workspace_slug))
            return roots
        memory_root = self.vault_dir / self._memory_subdir
        learnings_root = self.vault_dir / self._learnings_subdir
        blogposts_root = self.vault_dir / self._blogposts_subdir
        roots = [p for p in (memory_root, learnings_root, blogposts_root) if p.is_dir()]
        if include_checkpoints:
            roots.extend(self._checkpoint_roots(""))
        return roots

    def _checkpoint_roots(self, workspace_slug: str) -> list[Path]:
        if workspace_slug.strip():
            return [self.checkpoints_dir(workspace_slug)]
        checkpoints_root = self.vault_dir / self._checkpoints_subdir
        return [checkpoints_root] if checkpoints_root.is_dir() else []

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

        def _matches(notes: list[MemoryNote]) -> list[MemoryNote]:
            found: list[MemoryNote] = []
            for note in notes:
                haystack = f"{note.title}\n{note.body}\n{' '.join(note.tags)}".lower()
                if needle in haystack:
                    found.append(note)
            return found

        # Two passes: default roots stay checkpoint-blind; checkpoints get their
        # own walk so a Memory-filled list_notes(limit=500) cannot starve them.
        non_checkpoint = _matches(self.list_notes(workspace_slug=workspace_slug, limit=500))
        checkpoints = _matches(
            self.list_notes(
                workspace_slug=workspace_slug,
                include_checkpoints=True,
                note_type="checkpoint",
                limit=500,
            )
        )
        hits = non_checkpoint[:limit]
        remaining = limit - len(hits)
        if remaining > 0:
            hits.extend(checkpoints[:remaining])
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
        if title_match and title_match.start() == 0:
            # `upsert_note` writes the title back into the file as a leading
            # `# <title>` line, which is what this match just consumed. Leaving
            # it in `.body` would make the note disagree with the body its
            # writer passed — and with the graph copy of the same content,
            # which carries no such line. Only a match at position 0 is that
            # injected heading; a `#` heading further down is the author's.
            body = body[title_match.end() :].lstrip("\n")
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
            discredited=fields.get("discredited", "").strip().lower() == "true",
        )


class MemoryGraphStore:
    """Structured memory graph backed by SQLite (safe for iCloud when using DELETE journal)."""

    _NODE_COLUMNS = (
        "id, title, body, tags_json, ticket_id, workspace_slug, "
        "node_type, created_at, updated_at, discredited"
    )
    _VISIBLE_NODES = "COALESCE(discredited, 0) = 0"

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
                    updated_at TEXT NOT NULL,
                    discredited INTEGER NOT NULL DEFAULT 0
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
            self._ensure_discredited_column(conn)
            ensure_history_schema(conn)

    @staticmethod
    def _ensure_discredited_column(conn: sqlite3.Connection) -> None:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(memory_nodes)")}
        if "discredited" not in columns:
            conn.execute(
                "ALTER TABLE memory_nodes ADD COLUMN discredited INTEGER NOT NULL DEFAULT 0"
            )

    @staticmethod
    def _node_row(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "title": row["title"],
            "body": row["body"],
            "tags": json.loads(row["tags_json"] or "[]"),
            "ticket_id": row["ticket_id"],
            "workspace_slug": row["workspace_slug"],
            "node_type": row["node_type"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "discredited": bool(row["discredited"]),
        }

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
        discredited: bool | None = None,
        attribution: ChangeAttribution | None = None,
    ) -> dict[str, Any]:
        """Create or replace a node, preserving the version an update replaces.

        The prior version is written to `memory_node_versions` inside the same
        transaction as the update, so an update cannot land without its history
        (see `services.memory_history`). A create writes no history row.
        """
        node_id = node_id.strip() or str(uuid4())
        now = _utcnow_iso()
        tags_json = json.dumps(tags or [])
        with self._connect() as conn:
            row = conn.execute(
                "SELECT title, body, tags_json, updated_at, created_at, discredited "
                "FROM memory_nodes WHERE id = ?",
                (node_id,),
            ).fetchone()
            created_at = row["created_at"] if row else now
            if discredited is None:
                flag = int(row["discredited"]) if row else 0
            else:
                flag = 1 if discredited else 0
            if row:
                record_superseded(
                    conn,
                    node_id=node_id,
                    prior=row,
                    incoming=NodeContent(
                        title=title, body=body, tags_json=tags_json, discredited=bool(flag)
                    ),
                    superseded_at=now,
                    attribution=attribution or ChangeAttribution(),
                )
            conn.execute(
                """
                INSERT INTO memory_nodes (
                    id, title, body, tags_json, ticket_id, workspace_slug,
                    node_type, created_at, updated_at, discredited
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    title = excluded.title,
                    body = excluded.body,
                    tags_json = excluded.tags_json,
                    ticket_id = excluded.ticket_id,
                    workspace_slug = excluded.workspace_slug,
                    node_type = excluded.node_type,
                    updated_at = excluded.updated_at,
                    discredited = excluded.discredited
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
                    flag,
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
            "discredited": bool(flag),
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

    def get_node(self, node_id: str) -> dict[str, Any] | None:
        """One node by id, discredited or not — None only when it does not exist.

        An operator read: the discredit control acts on exactly the rows
        `_VISIBLE_NODES` hides, so this must not filter them.
        """
        with self._connect() as conn:
            row = conn.execute(
                f"SELECT {self._NODE_COLUMNS} FROM memory_nodes WHERE id = ?", (node_id,)
            ).fetchone()
        return self._node_row(row) if row else None

    def related_nodes(self, node_ids: list[str]) -> list[dict[str, Any]]:
        """Every visible node one `memory_relations` edge away, in both directions.

        A reader only (179): `memory_relations` is the sole edge store, and this
        writes nothing. Each row names the surfaced node it hangs off
        (`anchor_id`), the neighbour's title and `updated_at`, the edge's
        `relation_type`, and `direction` — ``out`` when the anchor is the
        edge's source, ``in`` when it is the target. No bodies: a digest that
        carried them would be a second briefing.

        Discredited neighbours are excluded here, like every other read path
        (182). Ranking and capping are the caller's — see
        `inherited_wisdom.rank_related`.
        """
        if not node_ids:
            return []
        marks = ", ".join("?" for _ in node_ids)
        visible = "COALESCE(n.discredited, 0) = 0"
        with self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT r.source_id AS anchor_id, n.id AS node_id, n.title, n.updated_at,
                       r.relation_type, 'out' AS direction
                FROM memory_relations r JOIN memory_nodes n ON n.id = r.target_id
                WHERE r.source_id IN ({marks}) AND {visible}
                UNION ALL
                SELECT r.target_id AS anchor_id, n.id AS node_id, n.title, n.updated_at,
                       r.relation_type, 'in' AS direction
                FROM memory_relations r JOIN memory_nodes n ON n.id = r.source_id
                WHERE r.target_id IN ({marks}) AND {visible}
                """,
                (*node_ids, *node_ids),
            ).fetchall()
        return [{**dict(row), "direction": RelationDirection(row["direction"])} for row in rows]

    def node_versions(self, node_id: str) -> list[dict[str, Any]]:
        """The retained superseded versions of a node, oldest first."""
        with self._connect() as conn:
            return list_versions(conn, node_id)

    def list_nodes(
        self,
        *,
        workspace_slug: str = "",
        limit: int = RECALL_CANDIDATE_CAP,
        include_discredited: bool = False,
    ) -> list[dict[str, Any]]:
        """Nodes in the workspace, newest first — enumeration, not matching.

        `search()` is the other reader of this table, and it is a
        `LIKE '%query%'` match. Ranking its output would rank whatever survived
        a whole-phrase substring test, i.e. almost always nothing, so recall
        needs a surface that filters on nothing except the discredited flag.
        The default is `RECALL_CANDIDATE_CAP`, mirroring the Obsidian side so
        the graph cannot become the new cost centre.

        `include_discredited` is for the operator surface only, which has to
        show the rows it can restore. Every agent read path takes the default.
        """
        slug = workspace_slug.strip()
        clauses = ["workspace_slug = ?"] if slug else []
        params: list[Any] = [slug] if slug else []
        if not include_discredited:
            clauses.append(self._VISIBLE_NODES)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT {self._NODE_COLUMNS}
                FROM memory_nodes
                {where}
                ORDER BY updated_at DESC
                LIMIT ?
                """,
                (*params, limit),
            ).fetchall()
        return [self._node_row(row) for row in rows]

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
                    f"""
                    SELECT {self._NODE_COLUMNS}
                    FROM memory_nodes
                    WHERE workspace_slug = ?
                      AND {self._VISIBLE_NODES}
                      AND (title LIKE ? OR body LIKE ? OR tags_json LIKE ?)
                    ORDER BY updated_at DESC
                    LIMIT ?
                    """,
                    (slug, needle, needle, needle, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    f"""
                    SELECT {self._NODE_COLUMNS}
                    FROM memory_nodes
                    WHERE {self._VISIBLE_NODES}
                      AND (title LIKE ? OR body LIKE ? OR tags_json LIKE ?)
                    ORDER BY updated_at DESC
                    LIMIT ?
                    """,
                    (needle, needle, needle, limit),
                ).fetchall()
        return [self._node_row(row) for row in rows]

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

    def store_readiness(self, *, workspace_slug: str) -> dict[MemoryStoreKind, MemoryStoreState]:
        """What each store *is*, sampled from configuration and the filesystem.

        MUST be called before any lookup runs. `MemoryGraphStore.__init__`
        mkdirs its parent and runs `_init_schema`, so merely constructing one
        CREATES an empty database — a wrong or unset graph path then reads back
        as a real store that happened to be empty. Answering from the path
        before anything is constructed is what keeps "silently absent" from
        being recorded as "read and empty".

        Reports only the three real stores. `MemoryStoreKind.SERVICE` never
        appears here: it names a factory failure, at which point there is no
        service to ask.

        The vault is stat'd, not inferred from being configured. Reporting READ
        because `self.obsidian` exists made a vault pointed at a path that does
        not exist the only READ in the set, and `classify` returns EMPTY the
        moment anything reads — so the operator was told "the vault was there
        and had nothing to say" when the truth was "the vault path is wrong".
        That is the counts-versus-states defect 183 removed, reappearing inside
        the sampler 183 added (545).

        Only the vault *root* is checked. The subdirectories are created on
        first write, so a vault that exists and has never been written to is
        genuinely readable and genuinely empty — which is EMPTY, correctly.

        Stat'ing here is safe for the ordering rule above: the directory
        accessors build paths and never `mkdir`. Only the write paths and
        `MemoryGraphStore.__init__` create anything, and none of them run here.
        """
        vault = (
            MemoryStoreState.READ
            if self.obsidian is not None and self.obsidian.vault_dir.is_dir()
            else MemoryStoreState.UNCONFIGURED
        )
        graph_path = self._graph_path_for_workspace(workspace_slug)
        return {
            MemoryStoreKind.CHECKPOINTS: vault,
            MemoryStoreKind.VAULT: vault,
            MemoryStoreKind.GRAPH: (
                MemoryStoreState.READ
                if graph_path is not None and graph_path.is_file()
                else MemoryStoreState.UNCONFIGURED
            ),
        }

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
        # Cutover R2 — GRAPH is the record; vault is a labelled export. Fail closed
        # without a graph: vault-only peer writes are not a valid path.
        graph = self._graph_for_workspace(workspace_slug)
        if not graph:
            raise ValueError(
                "Memory graph SQLite is not configured. Durable learnings require "
                "LOREGARDEN_MEMORY_SQLITE_URL (or iCloud defaults); vault-only writes "
                "are not allowed."
            )
        result: dict[str, Any] = {"ticket_id": ticket_id, "workspace_slug": workspace_slug}
        node = graph.upsert_node(
            title=f"Learning — {ticket_id}",
            body=content,
            tags=["learning", "loregarden", *(tags or [])],
            ticket_id=ticket_id,
            workspace_slug=workspace_slug,
            node_type="learning",
        )
        result["graph"] = node
        if self.obsidian:
            exported = export_node_to_vault(self, node=node)
            if exported:
                result["obsidian"] = exported
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
        discredited: bool | None = None,
    ) -> dict[str, Any]:
        graph = self._graph_for_workspace(workspace_slug)
        if not graph:
            raise ValueError(
                "Memory graph SQLite is not configured. Durable memory requires "
                "LOREGARDEN_MEMORY_SQLITE_URL (or iCloud defaults); vault-only writes "
                "are not allowed."
            )
        node_id = node_id.strip() or str(uuid4())
        result: dict[str, Any] = {}
        node = graph.upsert_node(
            node_id=node_id,
            title=title,
            body=body,
            tags=tags,
            ticket_id=ticket_id,
            workspace_slug=workspace_slug,
            node_type="memory",
            discredited=discredited,
        )
        result["graph"] = node
        if self.obsidian:
            exported = export_node_to_vault(self, node=node)
            if exported:
                result["obsidian"] = exported
        return result

    def _require_graph(self, workspace_slug: str) -> MemoryGraphStore:
        graph = self._graph_for_workspace(workspace_slug)
        if not graph:
            raise ValueError("Memory graph SQLite is not configured.")
        return graph

    def list_graph_nodes(
        self, *, workspace_slug: str, limit: int, include_discredited: bool
    ) -> list[dict[str, Any]]:
        """Operator listing of graph nodes. Agents use `recall_related`/`search`."""
        return self._require_graph(workspace_slug).list_nodes(
            workspace_slug=workspace_slug,
            limit=limit,
            include_discredited=include_discredited,
        )

    def node_detail(self, *, node_id: str, workspace_slug: str) -> dict[str, Any]:
        """One node, discredited or not, with its retained version history."""
        graph = self._require_graph(workspace_slug)
        node = graph.get_node(node_id)
        if node is None:
            raise MemoryNodeNotFoundError(node_id)
        return {**node, "versions": graph.node_versions(node_id)}

    def set_discredited(
        self,
        *,
        node_id: str,
        workspace_slug: str,
        discredited: bool,
        reason: str,
        writer: str,
    ) -> dict[str, Any]:
        """Mark a node wrong (or restore it), recording who and why.

        Goes through `upsert_node` like every other graph write, so the version
        it replaces — and the reason — land in `memory_node_versions`. The row
        is read back afterwards: a write that did not stick raises.
        """
        graph = self._require_graph(workspace_slug)
        node = graph.get_node(node_id)
        if node is None:
            raise MemoryNodeNotFoundError(node_id)
        graph.upsert_node(
            node_id=node_id,
            title=node["title"],
            body=node["body"],
            tags=node["tags"],
            ticket_id=node["ticket_id"],
            workspace_slug=node["workspace_slug"],
            node_type=node["node_type"],
            discredited=discredited,
            attribution=ChangeAttribution(writer=writer, note=reason),
        )
        stored = graph.get_node(node_id)
        if stored is None or stored["discredited"] is not discredited:
            raise MemoryWriteNotAppliedError(f"discredited={discredited} did not persist")
        if self.obsidian:
            export_node_to_vault(self, node=stored)
        return {**stored, "versions": graph.node_versions(node_id)}

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
        # Cutover R6 — envelope unchanged; membership filtered by kind.
        # Vault hits: blog_post | checkpoint only. Graph hits: memory | learning.
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
                if n.note_type in SEARCH_OBSIDIAN_KINDS
            ]
        if graph:
            graph_hits = [
                {**row, "source": "sqlite"}
                for row in graph.search(query, workspace_slug=workspace_slug, limit=limit)
                if row.get("node_type") in SEARCH_GRAPH_KINDS
            ]
        return {
            "query": query,
            "workspace_slug": workspace_slug.strip() or None,
            "obsidian": obsidian_hits,
            "graph": graph_hits,
        }

    # How much of a body the dedupe key reads. Long enough that two notes
    # sharing an opening paragraph do not collide, short enough that the key
    # stays cheap for `RECALL_CANDIDATE_CAP` candidates.
    _KEYED_BODY_CHARS = 200

    @staticmethod
    def _content_key(title: str, body: str) -> tuple[str, str]:
        """Identify one piece of remembered content for recall ranking.

        Shared node_ids mean vault export and graph share an id after cutover,
        but content-key dedupe remains for any pre-cutover residue still in a
        candidate list.
        """
        return (
            (title or "").strip().casefold(),
            " ".join((body or "").split())[: AgentMemoryService._KEYED_BODY_CHARS].casefold(),
        )

    def recall_related(
        self,
        query_text: str,
        *,
        workspace_slug: str = "",
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        """GRAPH-only durable knowledge whose wording overlaps ``query_text``.

        Cutover R5 — candidates come from SQLite alone. Blog posts and vault
        memory/learning exports are never recall inputs; hand-edits cannot
        change ranking. ``search()`` above stays a kind-filtered substring match
        for deliberate agent queries.
        """
        wanted = term_overlap.terms(query_text)
        if not wanted:
            # No terms, no hits — and crucially no graph file opened.
            return []

        candidates: list[dict[str, Any]] = []
        try:
            candidates += self._graph_candidates(workspace_slug)
        except Exception as exc:
            raise MemoryStoreReadError(MemoryStoreKind.GRAPH) from exc

        deduped: list[dict[str, Any]] = []
        seen: set[tuple[str, str]] = set()
        for row in term_overlap.rank_by_overlap(wanted, candidates):
            key = self._content_key(row["title"], row["body"])
            if key in seen:
                continue
            seen.add(key)
            deduped.append(row)
        return deduped[:limit]

    def related_digest_rows(
        self, node_ids: list[str], *, workspace_slug: str = ""
    ) -> list[dict[str, Any]]:
        """`MemoryGraphStore.related_nodes` for the workspace's graph, read-labelled.

        No graph configured means no edges to read, which is `[]` — the same
        answer `recall_related` gives, since no hits could have come from it.
        """
        try:
            graph = self._graph_for_workspace(workspace_slug)
            return graph.related_nodes(node_ids) if graph else []
        except Exception as exc:
            raise MemoryStoreReadError(MemoryStoreKind.GRAPH) from exc

    def _graph_candidates(self, workspace_slug: str) -> list[dict[str, Any]]:
        graph = self._graph_for_workspace(workspace_slug)
        if not graph:
            return []
        return [
            {
                "id": row["id"],
                "source": "sqlite",
                "title": row["title"],
                "body": row["body"],
                "tags": row["tags"],
                "updated_at": row["updated_at"],
            }
            for row in graph.list_nodes(workspace_slug=workspace_slug, limit=RECALL_CANDIDATE_CAP)
        ]
