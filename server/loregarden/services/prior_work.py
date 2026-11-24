"""Find what was already attempted on tickets like this one.

Every run starts from the ticket and the code, so work that was already done —
and mistakes already made — are invisible unless someone remembers them. The
most useful thing about a finished ticket is often not that it succeeded but
what it hit on the way, which lives in its error artifacts.

**An empty answer ships with the basis for its own emptiness.** Three
different facts used to return the same bare `[]`: the query tokenised to no
terms, the workspace slug did not resolve, and the scan matched nothing. A
caller could not tell them apart, so all three read as "nothing like this has
been tried" — and the most expensive of them, a typo in a slug, read as the
cheapest. The return is now `{results, basis}`; see `services/retrieval_basis`.

The three counts stay three. The scan is bounded at `_MAX_SCANNED` with no
`order_by`, so one `rows_in_scope` field holding the limit would tell every
downstream stage that an unexamined corpus was examined.
"""

from __future__ import annotations

from collections import Counter
from typing import Any

from loregarden.models.domain import Artifact, Ticket, TicketState, Workspace, comparable_utc
from loregarden.models.domain.enums import (
    MemoryStoreKind,
    MemoryStoreState,
    RetrievalEmptiness,
)
from loregarden.services.retrieval_basis import (
    Reduction,
    RetrievalBasis,
    reduce_to,
    untouched,
)
from loregarden.services.term_overlap import TermSelection, select_terms, terms
from sqlmodel import Session, func, select

MAX_RESULTS = 5
_MAX_SCANNED = 400
_MAX_ARTIFACTS = 4

#: This path reads no memory store, so every leg is NOT_QUERIED — which is
#: neither READ (nothing was read) nor UNCONFIGURED (the stores exist).
_LEGS_NOT_QUERIED = {
    MemoryStoreKind.VAULT.value: MemoryStoreState.NOT_QUERIED,
    MemoryStoreKind.GRAPH.value: MemoryStoreState.NOT_QUERIED,
    MemoryStoreKind.CHECKPOINTS.value: MemoryStoreState.NOT_QUERIED,
}


def _artifact_digest(session: Session, ticket_id: str, workspace_id: str) -> list[dict[str, str]]:
    """A few artifacts from the prior ticket, errors first.

    What went wrong is more use to someone about to repeat the work than what
    eventually went right.

    Scoped by workspace at the join AND again in the outer filter. A digest is
    the one part of a hit that quotes another ticket's error text verbatim, so
    joining on `ticket_id` alone — unique though ids are — leaves the scope of
    this query resting on a uniqueness claim rather than on a predicate.
    """
    rows = session.exec(
        select(Artifact)
        .join(Ticket, (Ticket.id == Artifact.ticket_id) & (Ticket.workspace_id == workspace_id))
        .where(Artifact.ticket_id == ticket_id, Ticket.workspace_id == workspace_id)
    ).all()
    rows = sorted(rows, key=lambda a: (a.kind != "error", comparable_utc(a.created_at)))
    return [
        {"kind": a.kind, "title": a.title or "", "evidence_kind": a.evidence_kind or ""}
        for a in rows[:_MAX_ARTIFACTS]
    ]


def _basis(
    *,
    corpus_total: int,
    scanned: int,
    emptiness: RetrievalEmptiness,
    selection: TermSelection | None = None,
    untouched_sentence: str = "",
    reductions: dict[str, Reduction] | None = None,
) -> RetrievalBasis:
    return RetrievalBasis(
        corpus_total=corpus_total,
        scanned=scanned,
        not_examined=corpus_total - scanned,
        legs=_LEGS_NOT_QUERIED,
        emptiness=emptiness,
        dropped_terms=dict(selection.dropped) if selection else {},
        ceiling_applied=bool(selection and selection.ceiling_applied),
        untouched_sentence=untouched_sentence,
        reductions=dict(reductions or {}),
    )


def _zero_hit_areas(candidates: list[tuple[Ticket, set[str]]], matched_ids: set[str]) -> list[str]:
    """The largest milestones inside the scanned rows that produced no hit.

    Grouped over rows already in scope — no second query, and no id lookup
    either: `Ticket.milestone` is a name already on the row. The parent id was
    the other candidate and is wrong for a sentence a person or an agent
    reads, because a UUID names nothing. Rows with no milestone are not an
    area and are left out rather than lumped into one.

    Largest first, because the caller names at most three and the three
    biggest unsearched areas are the three worth knowing about.
    """
    totals: Counter[str] = Counter()
    hits: Counter[str] = Counter()
    for ticket, _ in candidates:
        area = ticket.milestone.strip()
        if not area:
            continue
        totals[area] += 1
        if ticket.id in matched_ids:
            hits[area] += 1
    return [area for area, _ in totals.most_common() if hits[area] == 0]


def search_prior_work(
    session: Session,
    query: str,
    *,
    workspace_slug: str = "",
    exclude_ticket_id: str = "",
    limit: int = MAX_RESULTS,
) -> dict[str, Any]:
    """Finished tickets whose wording overlaps `query`, with the basis for the
    answer's size.

    Keyword overlap rather than anything cleverer: it is explainable, needs no
    index to maintain, and a wrong hit costs a glance rather than a bad
    decision. Only settled tickets are returned — an in-flight one has not
    established anything yet.

    Nothing here is memoized. Both retrieval paths re-read per call, which is
    the pin for the cache-key rule handed to lg-code-knowledge-284: there is no
    index row and no run stamp in this tree to key one on yet.
    """
    settled = [TicketState.DONE, TicketState.WONT_DO]
    statement = select(Ticket).where(Ticket.state.in_(settled))
    counting = select(func.count()).select_from(Ticket).where(Ticket.state.in_(settled))

    slug = workspace_slug.strip()
    if slug:
        workspace = session.exec(select(Workspace).where(Workspace.slug == slug)).first()
        if not workspace:
            # The most expensive of the three emptinesses, and the one that
            # used to be indistinguishable from "nothing has been tried".
            return {
                "results": [],
                "basis": _basis(
                    corpus_total=0,
                    scanned=0,
                    emptiness=RetrievalEmptiness.UNRESOLVED_SCOPE,
                ),
            }
        statement = statement.where(Ticket.workspace_id == workspace.id)
        counting = counting.where(Ticket.workspace_id == workspace.id)

    corpus_total = int(session.exec(counting).one())

    if not terms(query):
        # The corpus was never consulted, so `scanned` is 0 and everything in
        # it is `not_examined`. Reporting the count anyway is what stops the
        # reply implying the scope is empty.
        return {
            "results": [],
            "basis": _basis(
                corpus_total=corpus_total,
                scanned=0,
                emptiness=RetrievalEmptiness.NO_TERMS,
            ),
        }

    # One pass: tokenize every candidate once, and count document frequency
    # from that same pass. No second query and no index.
    candidates: list[tuple[Ticket, set[str]]] = []
    frequency: Counter[str] = Counter()
    for ticket in session.exec(statement.limit(_MAX_SCANNED)).all():
        candidate_terms = terms(f"{ticket.title} {ticket.description}")
        candidates.append((ticket, candidate_terms))
        frequency.update(candidate_terms)
    scanned = len(candidates)

    selection = select_terms(query, document_frequency=dict(frequency), corpus_total=scanned)
    wanted = selection.kept

    scored: list[tuple[int, Ticket, set[str]]] = []
    for ticket, candidate_terms in candidates:
        if exclude_ticket_id and ticket.id == exclude_ticket_id:
            continue
        overlap = wanted & candidate_terms
        if overlap:
            scored.append((len(overlap), ticket, overlap))

    scored.sort(key=lambda row: row[0], reverse=True)
    emitted = scored[:limit]
    # Every ticket that MATCHED, not every ticket that fitted in the reply.
    # Built from `emitted` this said "Nothing matched under milestone-B" in
    # the same basis whose reductions['results'].omitted named a milestone-B
    # ticket — one reply making two opposite claims, which is this ticket's
    # own failure class one layer in.
    matched_ids = {ticket.id for _, ticket, _ in scored}
    # `not_examined` counts rows the scan never reached. This counts matches
    # it *did* reach and then dropped, which is a different fact and the one
    # a caller needs to know the answer was capped rather than exhausted. The
    # omitted external ids travel inline, so a caller that wants a dropped
    # match can ask for it by name.
    results_reduction = reduce_to(
        [ticket.external_id for _, ticket, _ in scored],
        [ticket.external_id for _, ticket, _ in emitted],
        reason=f"the reply carries at most {limit} results, best match first",
        key=str,
    )
    sentence = untouched(
        _zero_hit_areas(candidates, matched_ids),
        scope=f"the {scanned} settled tickets scanned in {slug or 'every workspace'}",
    )

    results = [
        {
            "external_id": ticket.external_id,
            "title": ticket.title,
            "state": ticket.state.value,
            "matched_terms": sorted(overlap),
            "artifacts": _artifact_digest(session, ticket.id, ticket.workspace_id),
        }
        for _, ticket, overlap in emitted
    ]
    return {
        "results": results,
        "basis": _basis(
            corpus_total=corpus_total,
            scanned=scanned,
            emptiness=(RetrievalEmptiness.MATCHED if results else RetrievalEmptiness.NO_OVERLAP),
            selection=selection,
            reductions={"results": results_reduction},
            untouched_sentence=sentence,
        ),
    }
