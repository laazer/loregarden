"""One-way vault export of GRAPH memory/learning nodes.

The vault is not a second record: exports carry the graph ``node_id`` and
``derived: true``. Hand-edits never write back. ``rebuild_workspace_exports``
projects every graph node into markdown and quarantines vault files whose id
is absent from the graph under ``{Memory|Learnings}/_orphans/{workspace}/``.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import TYPE_CHECKING, Any

from loregarden.services.memory_authority import SEARCH_GRAPH_KINDS

if TYPE_CHECKING:
    from loregarden.services.memory_store import AgentMemoryService, ObsidianMemoryStore


def export_node_to_vault(
    service: AgentMemoryService,
    *,
    node: dict[str, Any],
) -> dict[str, Any] | None:
    """Write (or refresh) one graph node as a labelled vault export.

    Returns the obsidian result block, or None when no vault is configured.
    """
    if not service.obsidian:
        return None
    node_type = node.get("node_type") or "memory"
    note = service.obsidian.upsert_note(
        note_id=node["id"],
        title=node.get("title") or "",
        body=node.get("body") or "",
        tags=list(node.get("tags") or []),
        ticket_id=node.get("ticket_id") or "",
        workspace_slug=node.get("workspace_slug") or "",
        note_type=node_type,
        discredited=bool(node.get("discredited")),
        derived=True,
        aliases=list(node.get("aliases") or []),
    )
    return {"id": note.id, "path": note.path, "updated_at": note.updated_at}


def _move_to_orphans(path: Path, orphan_root: Path) -> None:
    orphan_root.mkdir(parents=True, exist_ok=True)
    dest = orphan_root / path.name
    if dest.exists():
        dest = orphan_root / f"{path.stem}-{path.stat().st_mtime_ns}{path.suffix}"
    shutil.move(str(path), str(dest))


def _quarantine_workspace_orphans(
    store: ObsidianMemoryStore,
    *,
    slug: str,
    graph_ids: set[str],
) -> int:
    """Move vault memory/learning files whose id is absent from the graph."""
    quarantined = 0
    trees = (
        (store.memory_dir(slug), store.vault_dir / store._memory_subdir),
        (store.learnings_dir(slug), store.vault_dir / store._learnings_subdir),
    )
    for workspace_dir, kind_root in trees:
        if not workspace_dir.is_dir():
            continue
        for path in list(workspace_dir.rglob("*.md")):
            if "_orphans" in path.parts:
                continue
            note = store._read_note(path)
            note_id = note.id if note else ""
            if note_id in graph_ids:
                continue
            _move_to_orphans(path, kind_root / "_orphans" / slug)
            quarantined += 1
    return quarantined


def rebuild_workspace_exports(
    service: AgentMemoryService,
    *,
    workspace_slug: str,
) -> dict[str, Any]:
    """Project every graph memory/learning node into the vault; quarantine orphans.

    Returns at least ``{"exported": N, "quarantined": M}``. Delete is forbidden —
    unmatched vault files move under ``_orphans/{workspace}/``.
    """
    slug = workspace_slug.strip()
    if not slug:
        raise ValueError("workspace_slug is required for rebuild_workspace_exports")

    graph = service._graph_for_workspace(slug)
    if not graph:
        raise ValueError("Memory graph SQLite is not configured; cannot rebuild exports.")
    if not service.obsidian:
        raise ValueError("Obsidian vault is not configured; cannot rebuild exports.")

    nodes = [
        row
        for row in graph.list_nodes(workspace_slug=slug, limit=100_000)
        if row.get("node_type") in SEARCH_GRAPH_KINDS
    ]
    graph_ids = {row["id"] for row in nodes}
    for node in nodes:
        export_node_to_vault(service, node=node)

    quarantined = _quarantine_workspace_orphans(service.obsidian, slug=slug, graph_ids=graph_ids)
    return {"exported": len(nodes), "quarantined": quarantined, "workspace_slug": slug}
