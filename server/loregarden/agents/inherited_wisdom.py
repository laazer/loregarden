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
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from time import perf_counter
from typing import Any

from loregarden.models.domain import Ticket
from loregarden.models.domain.enums import MemoryStoreKind, MemoryStoreState, RelationDirection
from loregarden.services import term_overlap
from loregarden.services.learning_confidence import UNOBSERVED, LearningConfidence, describe
from loregarden.services.memory_store import (
    CHECKPOINT_ENTRY_DELIMITER,
    AgentMemoryService,
    MemoryStoreReadError,
    slugify,
)

logger = logging.getLogger(__name__)

#: A backstop against one pathological entry, not the budget that decides how
#: much history a stage inherits. `_MAX_CHECKPOINTS` and `_MAX_MEMORY_HITS` do
#: that, and 11 entries is what bounds the section's size.
#:
#: It was 3000, which was the binding constraint instead. Built against every
#: one of the 927 real briefings the vault can currently assemble, with nothing
#: truncated: median 1643 chars, p90 2723, p99 5771, longest 9678. At 3000 the
#: cap cut 81 of them and dropped 128,591 characters of recorded decisions on
#: the floor; nothing at all truncates above 12,000. 16,000 leaves room for the
#: entries to grow — and for the real learnings that currently lose recall slots
#: to test residue, which are longer than the residue they will replace.
#:
#: Truncation is still reported (`truncated`, `pre_truncation_chars`), so the day
#: this binds again it says so rather than silently shortening the briefing.
MAX_WISDOM_CHARS = 16000
_MAX_CHECKPOINTS = 6
_MAX_MEMORY_HITS = 5
#: The 1-hop digest's hard caps (179). Per surfaced learning, then per briefing:
#: a densely connected node gets its best few neighbours and no more, and the
#: whole digest cannot outgrow a couple of learnings' worth of text however
#: many edges exist.
MAX_RELATED_PER_LEARNING = 3
MAX_RELATED_CHARS = 1200
_MAX_RELATED_TITLE = 120
_FRONTMATTER = re.compile(r"\A---\n.*?\n---\n", re.S)
_LOG_HEADING = re.compile(r"^# Checkpoint log —.*$", re.M)

#: The stores `store_readiness` reports on — every kind except the factory.
_READY_STORES = (MemoryStoreKind.CHECKPOINTS, MemoryStoreKind.VAULT, MemoryStoreKind.GRAPH)
#: Confidence for a set of graph node ids (178). Supplied by the caller, which
#: owns the database session this module deliberately never imports.
ConfidenceLookup = Callable[[Sequence[str]], Mapping[str, LearningConfidence]]

#: Durable recall consults GRAPH only (Cutover R5/R8). Checkpoints still read
#: the vault separately; VAULT is not a durable-memory recall store.
_RECALL_STORES = (MemoryStoreKind.GRAPH,)


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
    #: Graph node ids of the learnings that reached `text`, in briefing order.
    #: What `record_briefing` links to the run (178). Only learnings whose line
    #: survived truncation: one cut off the prompt was not surfaced.
    learning_node_ids: tuple[str, ...] = ()
    related_injected: int = 0
    #: True when a confidence lookup was supplied and failed, so the learnings
    #: went out unranked and unannotated rather than silently looking unrated.
    confidence_unavailable: bool = False

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

    entries: list[Any]
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
    # One mtime ordering across every candidate directory, not one per directory.
    # Iterating the candidate set filled the cap from whichever directory the set
    # happened to yield first, so on the 23 tickets whose checkpoints live under
    # more than one slug — an id dir and an external-id dir, from either side of
    # the id restructure — which run reached the prompt depended on set ordering.
    logs = sorted(
        (path for slug in candidates for path in (base / slug).glob("*.md")),
        # Path breaks an mtime tie, so two logs written in the same instant still
        # order the same way on every read rather than by glob order.
        key=lambda p: (p.stat().st_mtime, str(p)),
        reverse=True,
    )
    entries: list[str] = []
    for path in logs:
        body = _LOG_HEADING.sub("", _FRONTMATTER.sub("", path.read_text(encoding="utf-8")))
        entries.extend(_split_entries(body))
        if len(entries) >= _MAX_CHECKPOINTS:
            return entries[:_MAX_CHECKPOINTS]
    return entries[:_MAX_CHECKPOINTS]


def _split_entries(body: str) -> list[str]:
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


def _recall_query(ticket: Ticket) -> str:
    """The exact text `_memory_hits` searches on.

    Title *and* description: a title alone is a handful of terms, several of
    them stopwords, and the description is where a ticket says what it is
    actually about.
    """
    return " ".join(part for part in (ticket.title, ticket.description) if part).strip()


def _memory_hits(
    memory: AgentMemoryService, ticket: Ticket, workspace_slug: str
) -> list[dict[str, Any]]:
    """Graph learnings and memory notes whose text overlaps this ticket, best first."""
    query = _recall_query(ticket)
    if not query:
        return []
    found = memory.recall_related(query, workspace_slug=workspace_slug, limit=_MAX_MEMORY_HITS)
    return [row for row in found if str(row.get("title") or "").strip()][:_MAX_MEMORY_HITS]


def rank_learnings(
    rows: list[dict[str, Any]], confidence: Mapping[str, LearningConfidence]
) -> list[dict[str, Any]]:
    """Order recalled learnings by the lower bound of their confidence (178).

    Stable, so learnings with equal bounds — every unobserved one — keep their
    relevance order. The lower bound rather than the mean is what makes this
    sample-size aware: one lucky clean pass does not outrank a long record.
    """
    return sorted(rows, key=lambda row: -confidence.get(row["id"], UNOBSERVED).lower_bound)


def rank_related(
    rows: list[dict[str, Any]], confidence: Mapping[str, LearningConfidence]
) -> list[dict[str, Any]]:
    """THE ranking swap point for the 1-hop digest (179).

    By confidence lower bound, then recency. Change how neighbours are chosen
    here and nowhere else.
    """
    by_recency = sorted(rows, key=lambda row: row.get("updated_at") or "", reverse=True)
    return sorted(
        by_recency, key=lambda row: -confidence.get(row["node_id"], UNOBSERVED).lower_bound
    )


def _learning_line(row: dict[str, Any], confidence: LearningConfidence | None) -> str:
    title = str(row.get("title") or "").strip()
    summary = " ".join(str(row.get("body") or "").split())[:240]
    line = f"- **{title}** — {summary}" if summary else f"- **{title}**"
    return f"{line} _({describe(confidence)})_" if confidence is not None else line


_ARROWS = {RelationDirection.OUT: "→", RelationDirection.IN: "←"}


def _related_lines(
    anchors: list[str],
    related: list[dict[str, Any]],
    confidence: Mapping[str, LearningConfidence],
) -> dict[str, list[str]]:
    """The digest lines for each surfaced learning, under both caps."""
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in related:
        if row["node_id"] != row["anchor_id"]:
            grouped.setdefault(row["anchor_id"], []).append(row)
    lines: dict[str, list[str]] = {}
    budget = MAX_RELATED_CHARS
    for anchor in anchors:
        for row in rank_related(grouped.get(anchor, []), confidence)[:MAX_RELATED_PER_LEARNING]:
            title = " ".join(str(row["title"]).split())[:_MAX_RELATED_TITLE]
            line = f"  - {_ARROWS[row['direction']]} _{row['relation_type']}_: {title}"
            if len(line) + 1 > budget:
                return lines
            budget -= len(line) + 1
            lines.setdefault(anchor, []).append(line)
    return lines


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
    confidence_lookup: ConfidenceLookup | None = None,
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
        default_store=MemoryStoreKind.GRAPH,
    )

    anchors = [row["id"] for row in hits.entries]
    related = (
        _safely(
            lambda m, _t, slug: m.related_digest_rows(anchors, workspace_slug=slug),
            store,
            ticket,
            workspace_slug,
            label="related learnings",
            default_store=MemoryStoreKind.GRAPH,
        )
        if anchors
        else _Lookup(entries=[])
    )

    errors: list[str] = []
    for lookup in (checkpoints, hits, related):
        if lookup.store is None:
            continue
        states[lookup.store] = MemoryStoreState.ERRORED
        errors.append(f"{lookup.store.value}:{lookup.error}")

    confidence, confidence_unavailable = _confidences(
        confidence_lookup, [*anchors, *(row["node_id"] for row in related.entries)], ticket
    )
    ranked = rank_learnings(hits.entries, confidence)
    digest = _related_lines([row["id"] for row in ranked], related.entries, confidence)
    annotate = confidence_lookup is not None and not confidence_unavailable
    learning_lines = [
        _learning_line(row, confidence.get(row["id"], UNOBSERVED) if annotate else None)
        for row in ranked
    ]
    learning_blocks = [
        "\n".join([line, *digest.get(row["id"], [])])
        for line, row in zip(learning_lines, ranked, strict=True)
    ]

    joined = _assemble(checkpoints.entries, learning_blocks)
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
        learning_node_ids=tuple(
            row["id"] for line, row in zip(learning_lines, ranked, strict=True) if line in text
        ),
        related_injected=sum(len(lines) for lines in digest.values()),
        confidence_unavailable=confidence_unavailable,
    )


def _confidences(
    lookup: ConfidenceLookup | None, node_ids: list[str], ticket: Ticket
) -> tuple[Mapping[str, LearningConfidence], bool]:
    """Confidence for these nodes, and whether the lookup failed.

    A failed lookup degrades to an unranked, unannotated section — the
    briefing is never-fatal — and says so on the result and in the log, so it
    cannot pass for a corpus in which nothing has been observed yet.
    """
    if lookup is None or not node_ids:
        return {}, False
    try:
        return lookup(node_ids), False
    except Exception:  # noqa: BLE001 - optional signal; reported on the result and logged
        logger.warning(
            "Inherited wisdom: confidence unavailable for ticket %s", ticket.id, exc_info=True
        )
        return {}, True


def _bullet(entry: str) -> str:
    """One checkpoint as one list item, however many lines it spans.

    A checkpoint is a heading and three fields. Before entries were delimited
    each of those arrived here as its own entry and a flat `- {entry}` was
    accurate; now that the whole thing is one entry, continuation lines have to
    be indented under the marker or the blank line between its fields ends the
    list and the fields read as loose prose belonging to nothing.
    """
    head, *rest = entry.splitlines()
    return "\n".join([f"- {head}", *(f"  {line}" if line.strip() else "" for line in rest)])


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
        lines += [_bullet(entry) for entry in checkpoints]
    if hits:
        lines += ["", "### Related learnings"]
        lines += hits
    return "\n".join(lines)
