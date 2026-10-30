"""A ticket's checkpoint logs, read back from the vault.

`AgentMemoryService.append_checkpoint` writes one log per ticket and run
(`<checkpoints>/<ticket-slug>/<run-slug>.md`), each entry introduced by
`CHECKPOINT_ENTRY_DELIMITER`. Two readers need them back: the stage briefing
(`agents.inherited_wisdom`, the newest few entries) and the living map (every
entry, each as an item of its own), so the reading lives here once.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from loregarden.models.domain import Ticket
from loregarden.services.memory_store import (
    CHECKPOINT_ENTRY_DELIMITER,
    ObsidianMemoryStore,
    slugify,
)

_FRONTMATTER = re.compile(r"\A---\n.*?\n---\n", re.S)
_LOG_HEADING = re.compile(r"^# Checkpoint log —.*$", re.M)


@dataclass(frozen=True)
class CheckpointLog:
    """One run's log: its file's stem (the slug of the run id it was written
    under), when it was last written, and its entries, oldest first."""

    run_slug: str
    path: Path
    modified: float
    entries: tuple[str, ...]


def ticket_checkpoint_logs(
    store: ObsidianMemoryStore, ticket: Ticket, workspace_slug: str
) -> list[CheckpointLog]:
    """Every checkpoint log for this ticket, newest run first."""
    base = store.checkpoints_dir(workspace_slug)
    # append_checkpoint slugs whatever identifier the caller passed, and the MCP
    # tool accepts either form, so look under both.
    # The legacy id is in the set because checkpoints written before the id
    # restructure live under a directory named for the id of that day, and the
    # vault is outside anything a migration could rename.
    candidates = {
        slugify(ticket.id),
        slugify(ticket.external_id or ""),
        slugify(ticket.legacy_external_id or ""),
    } - {""}
    # One mtime ordering across every candidate directory, not one per directory.
    # Iterating the candidate set filled the cap from whichever directory the set
    # happened to yield first, so on the 23 tickets whose checkpoints live under
    # more than one slug — an id dir and an external-id dir, from either side of
    # the id restructure — which run reached the prompt depended on set ordering.
    paths = sorted(
        (path for slug in candidates for path in (base / slug).glob("*.md")),
        # Path breaks an mtime tie, so two logs written in the same instant still
        # order the same way on every read rather than by glob order.
        key=lambda p: (p.stat().st_mtime, str(p)),
        reverse=True,
    )
    logs: list[CheckpointLog] = []
    for path in paths:
        body = _LOG_HEADING.sub("", _FRONTMATTER.sub("", path.read_text(encoding="utf-8")))
        logs.append(
            CheckpointLog(
                run_slug=path.stem,
                path=path,
                modified=path.stat().st_mtime,
                entries=tuple(split_checkpoint_entries(body)),
            )
        )
    return logs


def split_checkpoint_entries(body: str) -> list[str]:
    """One log body's checkpoint entries, newest-written last.

    `append_checkpoint` introduces each entry with `CHECKPOINT_ENTRY_DELIMITER`,
    so everything after one marker and before the next is exactly one entry,
    however many paragraphs it spans. Logs written before it did have no boundary
    but a blank line, which splits a multi-paragraph entry into fragments — so
    the blank line stays the fallback for the text ahead of the first marker, and
    only for that.

    In a log open across the change that text is the earlier stages of the run
    being briefed — the most valuable part — so it is split the old way rather
    than dropped or returned as one blob. In a log written entirely after the
    change it is the frontmatter remnant and the heading, and empty.
    """
    legacy_prefix, *delimited = body.split(CHECKPOINT_ENTRY_DELIMITER)
    entries = [para.strip() for para in legacy_prefix.split("\n\n") if para.strip()]
    entries.extend(chunk.strip() for chunk in delimited if chunk.strip())
    return entries
