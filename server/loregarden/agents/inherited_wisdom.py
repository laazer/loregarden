"""Prior decisions and learnings for a ticket, injected into the stage prompt.

Continuity was pull-only: a stage prompt carried `ticket.blocking_issues` and
nothing else, so checkpoints and learnings recorded by earlier stages stayed
invisible unless an agent thought to go looking for them. Agents that did not
ask re-derived decisions their predecessors had already made.

The briefing degrades rather than failing — memory is optional infrastructure
on synced network storage — but it now *reports* the degradation instead of
returning a bare "" that reads exactly like "no memory exists yet". It reports
what the stores WERE (read / unconfigured / errored / not queried), never what
its row counts imply.

This module deliberately imports no database module: the briefing is assembled
before anything is persisted, and its telemetry row is written by
`services.memory_briefing_telemetry` from the record returned here.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from time import perf_counter

from loregarden.models.domain import Ticket
from loregarden.models.domain.enums import MemoryStoreKind, MemoryStoreState
from loregarden.services import term_overlap
from loregarden.services.memory_store import (
    AgentMemoryService,
    MemoryStoreReadError,
    slugify,
)

logger = logging.getLogger(__name__)

MAX_WISDOM_CHARS = 3000
_MAX_CHECKPOINTS = 6
_MAX_MEMORY_HITS = 5
_FRONTMATTER = re.compile(r"\A---\n.*?\n---\n", re.S)
_LOG_HEADING = re.compile(r"^# Checkpoint log —.*$", re.M)

#: The stores `store_readiness` reports on — every kind except the factory.
_READY_STORES = (MemoryStoreKind.CHECKPOINTS, MemoryStoreKind.VAULT, MemoryStoreKind.GRAPH)
#: The two stores `recall_related` consults. Both are skipped wholesale when the
#: query tokenises to no terms, which is neither a read nor an absence.
_RECALL_STORES = (MemoryStoreKind.VAULT, MemoryStoreKind.GRAPH)


@dataclass(frozen=True, slots=True)
class InheritedWisdom:
    """The briefing, and the facts about how it was assembled.

    `store_states` is the honest half: it says what each store was, so an
    unopenable graph and an empty one cannot report the same thing. Row counts
    are a measurement, not a diagnosis — nothing downstream may infer
    error-versus-empty from them.
    """

    text: str
    checkpoints_injected: int = 0
    learnings_injected: int = 0
    checkpoints_saturated: bool = False
    learnings_saturated: bool = False
    query_had_terms: bool = False
    chars_injected: int = 0
    pre_truncation_chars: int = 0
    truncated: bool = False
    store_states: Mapping[MemoryStoreKind, MemoryStoreState] = field(default_factory=dict)
    store_errors: tuple[str, ...] = ()
    elapsed_ms: int = 0

    @classmethod
    def not_attempted(cls) -> InheritedWisdom:
        """The one value recorded when no briefing was assembled at all.

        Its `store_states` is empty because no store was consulted — not
        because three stores read as nothing.
        """
        return cls(text="")


@dataclass(frozen=True, slots=True)
class _Lookup:
    """One store read, and the label for the store if it failed. Module-private."""

    entries: list[str]
    store: MemoryStoreKind | None = None
    error: str = ""


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
    # The legacy id is in the set because checkpoints written before the id
    # restructure live under a directory named for the id of that day, and the
    # vault is outside anything a migration could rename.
    candidates = {
        slugify(ticket.id),
        slugify(ticket.external_id or ""),
        slugify(ticket.legacy_external_id or ""),
    } - {""}
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


def _recall_query(ticket: Ticket) -> str:
    """The exact text `_memory_hits` searches on.

    Title *and* description: a title alone is a handful of terms, several of
    them stopwords, and the description is where a ticket says what it is
    actually about.
    """
    return " ".join(part for part in (ticket.title, ticket.description) if part).strip()


def _memory_hits(memory: AgentMemoryService, ticket: Ticket, workspace_slug: str) -> list[str]:
    """Learnings and memory notes whose text overlaps this ticket."""
    query = _recall_query(ticket)
    if not query:
        return []
    found = memory.recall_related(query, workspace_slug=workspace_slug, limit=_MAX_MEMORY_HITS)
    hits: list[str] = []
    for row in found:
        title = str(row.get("title") or "").strip()
        summary = " ".join(str(row.get("body") or "").split())[:240]
        if title:
            hits.append(f"- **{title}** — {summary}" if summary else f"- **{title}**")
        if len(hits) >= _MAX_MEMORY_HITS:
            break
    return hits


def _safely(
    fetch,
    store: AgentMemoryService,
    ticket: Ticket,
    workspace_slug: str,
    *,
    label: str,
    default_store: MemoryStoreKind,
) -> _Lookup:
    """Run one lookup, degrading to nothing rather than taking the section down.

    Memory is optional infrastructure on synced network storage, and its graph
    lives in a per-workspace SQLite file that may not exist yet. The failure is
    still *named*: a labelled `MemoryStoreReadError` carries the store that
    actually failed, and anything else is attributed to the store this lookup
    was reading.
    """
    try:
        return _Lookup(entries=fetch(store, ticket, workspace_slug))
    except MemoryStoreReadError as exc:
        logger.warning(
            "Inherited wisdom: %s unavailable for ticket %s", label, ticket.id, exc_info=True
        )
        return _Lookup(entries=[], store=exc.store, error=type(exc.__cause__).__name__)
    except Exception as exc:  # noqa: BLE001 - optional infrastructure, never fatal
        logger.warning(
            "Inherited wisdom: %s unavailable for ticket %s", label, ticket.id, exc_info=True
        )
        return _Lookup(entries=[], store=default_store, error=type(exc).__name__)


def _elapsed_ms(started: float) -> int:
    return int(round((perf_counter() - started) * 1000))


def build_inherited_wisdom(
    ticket: Ticket,
    workspace_slug: str,
    *,
    memory: AgentMemoryService | None = None,
    max_chars: int = MAX_WISDOM_CHARS,
) -> InheritedWisdom:
    """Checkpoints and learnings this ticket already carries, plus how it went.

    Never raises. The vault is optional and lives on synced network storage, so
    an unconfigured, stalled, or unreadable store must degrade to a prompt
    without this section rather than failing the run — but the returned record
    says which of those three happened.
    """
    started = perf_counter()
    try:
        store = memory or AgentMemoryService.from_settings()
    except Exception as exc:  # noqa: BLE001 - optional infrastructure, never fatal
        logger.warning("Inherited wisdom unavailable for ticket %s", ticket.id, exc_info=True)
        # No service means no store was reached, which is a failure and not an
        # absence: a box whose vault env var is unset used to report zero errors
        # forever, which is this ticket's own opening scenario.
        return InheritedWisdom(
            text="",
            store_states=dict.fromkeys(_READY_STORES, MemoryStoreState.ERRORED),
            store_errors=(f"{MemoryStoreKind.SERVICE.value}:{type(exc).__name__}",),
            elapsed_ms=_elapsed_ms(started),
        )

    # Sampled BEFORE the lookups: `_memory_hits` constructs the graph store,
    # which creates the very file readiness is asking about.
    states = dict(store.store_readiness(workspace_slug=workspace_slug))
    query_had_terms = bool(term_overlap.terms(_recall_query(ticket)))
    if not query_had_terms:
        # `recall_related` returns before touching either store, so a store that
        # was configured and never opened is neither read nor absent.
        for kind in _RECALL_STORES:
            if states.get(kind) == MemoryStoreState.READ:
                states[kind] = MemoryStoreState.NOT_QUERIED

    # Guarded separately. The two come from different stores — checkpoints from
    # vault files, hits from a per-workspace SQLite graph — and sharing one guard
    # meant an unopenable graph silently took the checkpoints down with it.
    checkpoints = _safely(
        _checkpoint_entries,
        store,
        ticket,
        workspace_slug,
        label="checkpoints",
        default_store=MemoryStoreKind.CHECKPOINTS,
    )
    hits = _safely(
        _memory_hits,
        store,
        ticket,
        workspace_slug,
        label="learnings",
        default_store=MemoryStoreKind.VAULT,
    )

    errors: list[str] = []
    for lookup in (checkpoints, hits):
        if lookup.store is None:
            continue
        states[lookup.store] = MemoryStoreState.ERRORED
        errors.append(f"{lookup.store.value}:{lookup.error}")

    joined = _assemble(checkpoints.entries, hits.entries)
    text = joined[:max_chars]
    return InheritedWisdom(
        text=text,
        checkpoints_injected=len(checkpoints.entries),
        learnings_injected=len(hits.entries),
        checkpoints_saturated=len(checkpoints.entries) == _MAX_CHECKPOINTS,
        learnings_saturated=len(hits.entries) == _MAX_MEMORY_HITS,
        query_had_terms=query_had_terms,
        chars_injected=len(text),
        pre_truncation_chars=len(joined),
        # Measured against the pre-truncation length. `len(text) == max_chars`
        # would report every briefing that happens to land on the bound as
        # truncated, and the flag would stop meaning "context was lost".
        truncated=len(joined) > max_chars,
        store_states=states,
        store_errors=tuple(sorted(errors)),
        elapsed_ms=_elapsed_ms(started),
    )


def _assemble(checkpoints: list[str], hits: list[str]) -> str:
    """The prompt section, or "" when there is nothing to say."""
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
    return "\n".join(lines)
