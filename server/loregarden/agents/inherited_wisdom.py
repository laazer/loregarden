"""Prior decisions and learnings for a ticket, injected into the stage prompt.

Continuity was pull-only: a stage prompt carried `ticket.blocking_issues` and
nothing else, so checkpoints and learnings recorded by earlier stages stayed
invisible unless an agent thought to go looking for them. Agents that did not
ask re-derived decisions their predecessors had already made.
"""

from __future__ import annotations

import logging
import re

from loregarden.models.domain import Ticket
from loregarden.services.memory_store import AgentMemoryService, slugify

logger = logging.getLogger(__name__)

MAX_WISDOM_CHARS = 3000
_MAX_CHECKPOINTS = 6
_MAX_MEMORY_HITS = 5
_FRONTMATTER = re.compile(r"\A---\n.*?\n---\n", re.S)
_LOG_HEADING = re.compile(r"^# Checkpoint log —.*$", re.M)


def _checkpoint_entries(
    memory: AgentMemoryService, ticket: Ticket, workspace_slug: str
) -> list[str]:
    """Most recent checkpoint entries for this ticket, newest run first."""
    store = memory.obsidian
    if not store:
        return []

    base = store.checkpoints_dir(workspace_slug)
    # append_checkpoint slugs whatever identifier the caller passed, and the MCP
    # tool accepts either form, so look under both.
    candidates = {slugify(ticket.id), slugify(ticket.external_id or "")} - {""}
    entries: list[str] = []
    for slug in candidates:
        directory = base / slug
        if not directory.is_dir():
            continue
        for path in sorted(directory.glob("*.md"), key=lambda p: p.stat().st_mtime, reverse=True):
            body = _LOG_HEADING.sub("", _FRONTMATTER.sub("", path.read_text(encoding="utf-8")))
            entries.extend(chunk.strip() for chunk in body.split("\n\n") if chunk.strip())
            if len(entries) >= _MAX_CHECKPOINTS:
                return entries[:_MAX_CHECKPOINTS]
    return entries[:_MAX_CHECKPOINTS]


def _memory_hits(memory: AgentMemoryService, ticket: Ticket, workspace_slug: str) -> list[str]:
    """Learnings and memory notes whose text overlaps this ticket."""
    query = (ticket.title or "").strip()
    if not query:
        return []
    found = memory.search(query, workspace_slug=workspace_slug, limit=_MAX_MEMORY_HITS)
    hits: list[str] = []
    for row in [*found.get("obsidian", []), *found.get("graph", [])]:
        title = str(row.get("title") or "").strip()
        summary = " ".join(str(row.get("body") or "").split())[:240]
        if title:
            hits.append(f"- **{title}** — {summary}" if summary else f"- **{title}**")
        if len(hits) >= _MAX_MEMORY_HITS:
            break
    return hits


def _safely(
    fetch, store: AgentMemoryService, ticket: Ticket, workspace_slug: str, *, label: str
) -> list[str]:
    """Run one lookup, degrading to nothing rather than taking the section down.

    Memory is optional infrastructure on synced network storage, and its graph
    lives in a per-workspace SQLite file that may not exist yet.
    """
    try:
        return fetch(store, ticket, workspace_slug)
    except Exception:  # noqa: BLE001 - optional infrastructure, never fatal
        logger.warning(
            "Inherited wisdom: %s unavailable for ticket %s", label, ticket.id, exc_info=True
        )
        return []


def build_inherited_wisdom(
    ticket: Ticket,
    workspace_slug: str,
    *,
    memory: AgentMemoryService | None = None,
    max_chars: int = MAX_WISDOM_CHARS,
) -> str:
    """Checkpoints and learnings this ticket already carries, or "" if none.

    Never raises. The vault is optional and lives on synced network storage, so
    an unconfigured, stalled, or unreadable store must degrade to a prompt
    without this section rather than failing the run.
    """
    try:
        store = memory or AgentMemoryService.from_settings()
    except Exception:  # noqa: BLE001 - optional infrastructure, never fatal
        logger.warning("Inherited wisdom unavailable for ticket %s", ticket.id, exc_info=True)
        return ""

    # Guarded separately. The two come from different stores — checkpoints from
    # vault files, hits from a per-workspace SQLite graph — and sharing one guard
    # meant an unopenable graph silently took the checkpoints down with it.
    checkpoints = _safely(_checkpoint_entries, store, ticket, workspace_slug, label="checkpoints")
    hits = _safely(_memory_hits, store, ticket, workspace_slug, label="learnings")

    if not checkpoints and not hits:
        return ""

    lines = [
        "Decisions and context already recorded for this ticket. Treat them as",
        "settled unless you find evidence otherwise — do not re-derive them.",
    ]
    if checkpoints:
        lines += ["", "### Checkpoints from earlier stages"]
        lines += [f"- {entry}" for entry in checkpoints]
    if hits:
        lines += ["", "### Related learnings"]
        lines += hits
    return "\n".join(lines)[:max_chars]
