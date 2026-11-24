"""Where a workspace's memory stores are, what state they are in, and the
basis a search over them ships with.

Split out of `services/memory_store.py`, which had reached its size cap. The
three belong together: the path decides the scope, the readiness sample
decides whether the store may be opened at all, and the basis is what those
two facts become in a reply.

The ordering rule lives here and is the point of the module.
`MemoryGraphStore.__init__` mkdirs its parent and runs `_init_schema`, so
merely constructing one CREATES an empty database. Every answer here is
derived from the path and a `stat`, before anything is constructed — which is
what keeps "silently absent" from being recorded as "read, and empty".
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from loregarden.models.domain.enums import (
    MemoryStoreKind,
    MemoryStoreState,
    RetrievalEmptiness,
)
from loregarden.services.retrieval_basis import Reduction, RetrievalBasis, untouched
from loregarden.services.term_overlap import TermSelection

#: A workspace slug becomes a directory name under the vault, so it must be
#: one bare segment: no separator, no traversal, no NUL.
_BARE_SEGMENT = re.compile(r"[A-Za-z0-9_][A-Za-z0-9._-]*")


def require_bare_segment(workspace_slug: str) -> str:
    """The one slug rule. Returns the slug, or raises naming the refusal.

    Separated from `graph_path_for_workspace` so the rule can be asked
    *without* a configured base. `config.resolved_memory_sqlite_path` answers
    `None` when no graph is configured, and the vault half of
    `AgentMemoryService.status` slugifies rather than refusing — so a rule
    that only ran once a base existed would let `'./loregarden'` report
    another workspace's Memory, Learnings, BlogPosts and Checkpoints
    directories, every one of them inside the vault and so invisible to any
    assertion about escaping it.

    `''` is the only value that means "no slug given". Whitespace is caller
    text, and stripping it to empty is one MCP argument away from the shared
    multi-tenant base file.
    """
    if workspace_slug == "":
        return workspace_slug
    if not _BARE_SEGMENT.fullmatch(workspace_slug) or workspace_slug in {".", ".."}:
        raise ValueError(
            f"workspace_slug {workspace_slug!r} is not a bare path segment; "
            "it is refused rather than rewritten, because rewriting it would "
            "silently point this workspace at a different graph file"
        )
    return workspace_slug


def graph_path_for_workspace(base: Path | None, workspace_slug: str) -> Path | None:
    """The workspace's graph file, or the shared base when no slug is given.

    A slug that is not a bare path segment is REJECTED, never slugified. The
    slug becomes a directory name, so `../other` walks out of the vault — but
    slugifying it into something safe would be worse: it would quietly
    relocate a live workspace's graph file and make that workspace's memory
    read as empty, with nothing reporting it.

    An empty slug legitimately means "the shared base file" and keeps working.
    A slug of *whitespace* does not: it is caller text rather than an omitted
    argument, and stripping it to empty is one MCP argument away from reading
    every tenant's rows out of the multi-tenant base file, which
    `MemoryGraphStore.search` filters only for a non-empty slug.
    """
    require_bare_segment(workspace_slug)
    if not base:
        return None
    if workspace_slug == "":
        return base
    return base.parent / workspace_slug / base.name


def sample_readiness(
    *, vault_dir: Path | None, graph_path: Path | None
) -> dict[MemoryStoreKind, MemoryStoreState]:
    """What each store *is*, sampled from configuration and the filesystem.

    MUST be sampled before any lookup runs; see the module docstring for why.

    Reports only the three real stores. `MemoryStoreKind.SERVICE` never
    appears here: it names a factory failure, at which point there is no
    service to ask.

    The vault is stat'd, not inferred from being configured. Reporting READ
    because a store object exists made a vault pointed at a path that does not
    exist the only READ in the set, and `classify` returns EMPTY the moment
    anything reads — so the operator was told "the vault was there and had
    nothing to say" when the truth was "the vault path is wrong" (545).

    Only the vault *root* is checked. The subdirectories are created on first
    write, so a vault that exists and has never been written to is genuinely
    readable and genuinely empty — which is EMPTY, correctly.
    """
    vault = (
        MemoryStoreState.READ
        if vault_dir is not None and vault_dir.is_dir()
        else MemoryStoreState.UNCONFIGURED
    )
    return {
        MemoryStoreKind.CHECKPOINTS: vault,
        MemoryStoreKind.VAULT: vault,
        MemoryStoreKind.GRAPH: (
            MemoryStoreState.READ
            if graph_path is not None and graph_path.is_file()
            else MemoryStoreState.UNCONFIGURED
        ),
    }


def search_basis(
    *,
    workspace_slug: str,
    readiness: dict[MemoryStoreKind, MemoryStoreState],
    selection: TermSelection,
    node_type_counts: dict[str, int],
    corpus_total: int,
    scanned: int,
    graph_hits: list[dict[str, Any]],
    obsidian_hits: list[dict[str, Any]],
    reductions: dict[str, Reduction] | None = None,
) -> RetrievalBasis:
    """Why a memory search answered with the hits it did.

    `selection` is passed in rather than recomputed here: the caller searched
    on those terms, and a basis that re-derived them could describe a
    different query from the one that ran. `ceiling_applied` travels with it,
    because an empty `dropped_terms` means "nothing was too common" only when
    the ceiling ran — on this path it never does, since the memory graph has
    no corpus count, and saying so is the honest answer AC8 asks for.

    `corpus_total` and `scanned` are measured by the caller and cover every
    leg it actually read. They used to be derived here from the graph's node
    counts alone, which made a vault-only reply report `corpus_total == 0`
    beside its own hits — firing this module's "zero corpus carries no
    information" note against an answer that did match. `node_type_counts`
    still arrives, but only to name zero-hit kinds.

    `not_examined` is the difference and is a real number on this path: the
    vault has no predicate to narrow with, so its scan cap can leave notes
    unread. What the reply dropped *after* matching is a `Reduction`, which
    is a different fact and is reported separately.

    The untouched sentence names zero-hit kinds in the QUERIED workspace only.
    Naming them in other workspaces would mean opening their SQLite files,
    which is exactly the speculative work the readiness sample exists to
    avoid, so the sentence says which scope it covers.
    """
    hit_kinds = {hit.get("node_type") for hit in graph_hits}
    ranked = [kind for kind, _ in sorted(node_type_counts.items(), key=lambda pair: -pair[1])]
    return RetrievalBasis(
        corpus_total=corpus_total,
        scanned=scanned,
        not_examined=corpus_total - scanned,
        legs={kind.value: state for kind, state in readiness.items()},
        emptiness=search_emptiness(readiness, selection.kept, graph_hits, obsidian_hits),
        dropped_terms=dict(selection.dropped),
        ceiling_applied=selection.ceiling_applied,
        untouched_sentence=untouched(
            [kind for kind in ranked if kind not in hit_kinds],
            scope=f"workspace {workspace_slug.strip() or 'base'}",
        ),
        reductions=dict(reductions or {}),
    )


def search_emptiness(
    readiness: dict[MemoryStoreKind, MemoryStoreState],
    kept_terms: set[str],
    graph_hits: list[dict[str, Any]],
    obsidian_hits: list[dict[str, Any]],
) -> RetrievalEmptiness:
    """Which of the emptinesses this answer is, so no caller has to guess."""
    if graph_hits or obsidian_hits:
        return RetrievalEmptiness.MATCHED
    if not kept_terms:
        return RetrievalEmptiness.NO_TERMS
    if all(state is not MemoryStoreState.READ for state in readiness.values()):
        return RetrievalEmptiness.STORE_UNAVAILABLE
    return RetrievalEmptiness.NO_OVERLAP
