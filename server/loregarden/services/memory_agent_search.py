"""What an agent's question does to the two memory stores.

Split out of `services/memory_store.py`, which had reached its size cap, and
cohesive on its own terms: the reduction of the question, the search of each
leg on the resulting tokens, and the cap that becomes a reportable
`Reduction` are one subject. `services/memory_scope.py` is its neighbour and
holds the other half — where the stores are and what state they were in.

**The agent entry point reduces; the operator entry point does not.** An
agent does not type a substring, it asks a question, and a question matched
whole essentially never hits: a node titled "Retrieval basis for memory
store" was invisible to "how does the memory store build its retrieval basis"
and found by "retrieval". `MemoryGraphStore.search` stays a whole-phrase
match for `services/memory_browser.py`, because an operator typing a
substring means a substring.

No discriminativeness ceiling runs here. There is no corpus count on this
path, so the basis reports `ceiling_applied=False` rather than a share nobody
measured — which is the honest answer, and a different claim from "no term
was too common".
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, NamedTuple

from loregarden.models.domain.enums import MemoryStoreKind, MemoryStoreState
from loregarden.models.domain.memory_enums import MemoryNodeType
from loregarden.services.memory_authority import SEARCH_GRAPH_KINDS, SEARCH_OBSIDIAN_KINDS
from loregarden.services.memory_scope import search_basis
from loregarden.services.retrieval_basis import Reduction, reduce_to
from loregarden.services.sql_like import like_contains
from loregarden.services.term_overlap import select_terms

if TYPE_CHECKING:  # import cycle: these are this module's callers
    from loregarden.services.memory_store import (
        AgentMemoryService,
        MemoryGraphStore,
        MemoryNote,
        ObsidianMemoryStore,
    )

#: How many vault notes one leg reads before it stops. The vault has no
#: predicate to narrow with — matching means reading the file — so this bound
#: stays, and whatever it leaves unread is reported as `not_examined` rather
#: than silently dropped. The graph leg needs no equivalent: its predicate
#: runs in SQL, so it reads matches rather than the scope.
VAULT_SCAN_CAP = 500


def terms_matched(node: dict[str, Any], search_terms: set[str]) -> int:
    """How many distinct query terms a node's searchable text carries.

    Distinct rather than total, so a node repeating one word does not outrank
    one touching four — the same rule `term_overlap.rank_by_overlap` uses.
    """
    haystack = " ".join(
        [
            node.get("title") or "",
            node.get("body") or "",
            " ".join(node.get("tags") or []),
            " ".join(node.get("aliases") or []),
        ]
    ).lower()
    return sum(1 for term in search_terms if term in haystack)


def graph_rows_for_terms(
    store: MemoryGraphStore,
    search_terms: set[str],
    *,
    workspace_slug: str = "",
    node_type: MemoryNodeType | None = None,
    include_discredited: bool = False,
) -> list[dict[str, Any]]:
    """Visible nodes matching ANY of `search_terms`, best-matching first.

    The agent entry point, beside `search`, which stays a whole-phrase
    substring match for the operator Memory browser — somebody typing a
    substring expects a substring. An agent does not type: it asks a
    question, and a question LIKE'd whole essentially never matches. One
    node titled "Retrieval basis for memory store" is invisible to "how
    does the memory store build its retrieval basis" and found by
    "retrieval", which is the regression this method exists to remove.

    Every term carries its own ESCAPE. The allowlist tokenizer has already
    removed every character able to reshape an expression, so this is the
    second line rather than the only one — but a term is still caller text
    and `_` is still a wildcard.

    Ranked by how many distinct terms a row matched, so a node answering
    four terms outranks one answering a single common word.

    **Every match is read; there is no SQL cap here.** There was one, and it
    made the reply lie: `LIMIT 500` with `ORDER BY updated_at DESC` returned
    the 500 most recent matches and ranked only those, while the basis went
    on reporting `scanned == corpus_total` and `not_examined == 0`. With 520
    matches, twenty were never read, never ranked, and named nowhere — the
    emitted set reported as the matched set, which is this ticket's own
    failure class. The caller caps the reply and records what it dropped, so
    the cap is a `Reduction` whose `omitted` can actually name rows.

    The predicate is already narrow — one workspace, visible rows, at least
    one term — so this reads matches rather than the table.
    """
    if not search_terms:
        return []
    slug = workspace_slug.strip()
    ordered = sorted(search_terms)
    clauses = ["workspace_slug = ?"] if slug else []
    params: list[Any] = [slug] if slug else []
    if not include_discredited:
        clauses.append(store.VISIBLE_NODES)

    matched = []
    for term in ordered:
        needle = like_contains(term)
        matched.append(
            "(title LIKE ? ESCAPE ? OR body LIKE ? ESCAPE ? "
            "OR tags_json LIKE ? ESCAPE ? OR aliases_json LIKE ? ESCAPE ?)"
        )
        params.extend([needle.pattern, needle.escape] * 4)
    clauses.append(f"({' OR '.join(matched)})")
    if node_type is not None:
        clauses.append("node_type = ?")
        params.append(node_type.value)

    with store.connection() as conn:
        rows = conn.execute(
            f"""
            SELECT {store.NODE_COLUMNS}
            FROM memory_nodes
            WHERE {" AND ".join(clauses)}
            ORDER BY updated_at DESC
            """,  # noqa: S608 - only bind-parameter marks are interpolated
            tuple(params),
        ).fetchall()
    scored = [store.node_row(row) for row in rows]
    scored.sort(key=lambda node: terms_matched(node, search_terms), reverse=True)
    return scored


class VaultScan(NamedTuple):
    """What the vault leg matched, and how much of it was read to find out.

    Two fields because they answer two different questions, and collapsing
    them is how a leg reports its emitted set as its matched set: `matches`
    is everything that matched, `examined` is how many notes were read at
    all. The caller caps `matches` and records the difference.
    """

    matches: list[MemoryNote]
    examined: int


def vault_notes_for_terms(
    store: ObsidianMemoryStore,
    search_terms: set[str],
    *,
    workspace_slug: str = "",
    scan_cap: int = VAULT_SCAN_CAP,
) -> VaultScan:
    """Notes matching ANY of `search_terms`, for the agent entry point.

    The vault half of the same reduction the graph leg makes: an agent's
    question matches nothing as one contiguous substring, and reducing only
    one leg would make the two disagree about what was asked.

    Returns every match rather than the first `limit` of them. It used to
    truncate here and the caller recorded nothing, so a vault leg with 30
    matching blog posts answered five and reported no reduction at all —
    the graph leg's cap was made reportable and this identical one was not.
    """
    if not search_terms:
        return VaultScan(matches=[], examined=0)

    def _matches(notes: list[MemoryNote]) -> list[MemoryNote]:
        found: list[MemoryNote] = []
        for note in notes:
            haystack = f"{note.title}\n{note.body}\n{' '.join(note.tags)}".lower()
            if any(term in haystack for term in search_terms):
                found.append(note)
        return found

    # Two passes, as in `search`: default roots stay checkpoint-blind so a
    # Memory-filled scan cannot starve checkpoints.
    standard = store.list_notes(workspace_slug=workspace_slug, limit=scan_cap)
    checkpoints = store.list_notes(
        workspace_slug=workspace_slug,
        include_checkpoints=True,
        note_type="checkpoint",
        limit=scan_cap,
    )
    return VaultScan(
        matches=_matches(standard) + _matches(checkpoints),
        examined=len(standard) + len(checkpoints),
    )


def agent_search(
    service: AgentMemoryService,
    query: str,
    *,
    workspace_slug: str = "",
    limit: int = 20,
) -> dict[str, Any]:
    """Both legs searched on the reduced question, with the basis for the size.

    Cutover R6 — vault hits are blog_post | checkpoint, graph hits are
    memory | learning.

    `store_readiness` is sampled FIRST and decides whether the graph is opened
    at all. That ordering is the criterion, not an optimisation: constructing
    a `MemoryGraphStore` creates its file, so a basis built by opening the
    store would report READ for a store that was never configured — "silently
    absent" recorded as "read, and empty", which is this layer's own failure
    class.

    Each leg reports what it MATCHED and what it EMITTED separately. Those are
    different sets whenever a cap binds, and reporting the second as the first
    is the defect this whole module exists to avoid one level down.
    """
    readiness = service.store_readiness(workspace_slug=workspace_slug)
    selection = select_terms(query)
    obsidian_hits: list[dict[str, Any]] = []
    graph_hits: list[dict[str, Any]] = []
    reductions: dict[str, Reduction] = {}
    graph = (
        service.graph_for_workspace(workspace_slug)
        if readiness[MemoryStoreKind.GRAPH] is MemoryStoreState.READ
        else None
    )

    vault_corpus = vault_examined = 0
    if service.obsidian:
        vault_corpus = service.obsidian.count_notes(workspace_slug=workspace_slug)
        scan = vault_notes_for_terms(
            service.obsidian, selection.kept, workspace_slug=workspace_slug
        )
        vault_examined = scan.examined
        matched_notes = [n for n in scan.matches if n.note_type in SEARCH_OBSIDIAN_KINDS]
        emitted_notes = matched_notes[:limit]
        obsidian_hits = [_vault_hit(note) for note in emitted_notes]
        reductions["obsidian"] = reduce_to(
            [note.id for note in matched_notes],
            [note.id for note in emitted_notes],
            reason=f"the reply carries at most {limit} vault hits",
            key=str,
        )

    graph_corpus = 0
    if graph:
        counts = graph.node_type_counts(workspace_slug.strip())
        graph_corpus = sum(counts.values())
        matched_rows = [
            {**row, "source": "sqlite"}
            for row in graph.search_terms(selection.kept, workspace_slug=workspace_slug)
            if row.get("node_type") in SEARCH_GRAPH_KINDS
        ]
        graph_hits = matched_rows[:limit]
        reductions["graph"] = reduce_to(
            [row["id"] for row in matched_rows],
            [row["id"] for row in graph_hits],
            reason=f"the reply carries at most {limit} graph hits",
            key=str,
        )
    else:
        counts = {}

    return {
        "query": query,
        "workspace_slug": workspace_slug.strip() or None,
        "obsidian": obsidian_hits,
        "graph": graph_hits,
        "basis": search_basis(
            workspace_slug=workspace_slug,
            readiness=readiness,
            selection=selection,
            node_type_counts=counts,
            # Both legs, because a reply holding vault hits beside a
            # graph-only corpus_total of 0 fires the basis's own
            # "zero corpus carries no information" note against an answer
            # that did match.
            corpus_total=graph_corpus + vault_corpus,
            scanned=graph_corpus + vault_examined,
            graph_hits=graph_hits,
            obsidian_hits=obsidian_hits,
            reductions=reductions,
        ),
    }


def _vault_hit(note: MemoryNote) -> dict[str, Any]:
    return {
        "id": note.id,
        "title": note.title,
        "body": note.body,
        "tags": note.tags,
        "ticket_id": note.ticket_id,
        "workspace_slug": note.workspace_slug,
        "note_type": note.note_type,
        "path": note.path,
        "source": "obsidian",
    }
