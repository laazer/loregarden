"""Find what was already attempted on tickets like this one.

Every run starts from the ticket and the code, so work that was already done —
and mistakes already made — are invisible unless someone remembers them. The
most useful thing about a finished ticket is often not that it succeeded but
what it hit on the way, which lives in its error artifacts.
"""

from __future__ import annotations

import re

from loregarden.models.domain import Artifact, Ticket, TicketState, Workspace
from sqlmodel import Session, select

MAX_RESULTS = 5
_MAX_SCANNED = 400
_MIN_TERM_LEN = 4
_MAX_ARTIFACTS = 4

# Words that appear in most tickets here and so separate nothing.
_STOPWORDS = frozenset(
    {
        "with",
        "from",
        "that",
        "this",
        "when",
        "then",
        "have",
        "into",
        "make",
        "made",
        "does",
        "onto",
        "over",
        "your",
        "which",
        "while",
        "where",
        "loregarden",
        "ticket",
        "tickets",
        "stage",
        "stages",
        "agent",
        "agents",
        "should",
        "would",
        "could",
        "using",
        "used",
        "also",
        "some",
        "more",
        "fix",
        "fixes",
        "fixed",
        "add",
        "adds",
        "added",
        "update",
        "updates",
    }
)


def _terms(text: str) -> set[str]:
    words = re.findall(r"[a-zA-Z_][a-zA-Z0-9_]+", (text or "").lower())
    return {w for w in words if len(w) >= _MIN_TERM_LEN and w not in _STOPWORDS}


def _artifact_digest(session: Session, ticket_id: str) -> list[dict[str, str]]:
    """A few artifacts from the prior ticket, errors first.

    What went wrong is more use to someone about to repeat the work than what
    eventually went right.
    """
    rows = session.exec(select(Artifact).where(Artifact.ticket_id == ticket_id)).all()
    rows = sorted(rows, key=lambda a: (a.kind != "error", a.created_at))
    return [
        {"kind": a.kind, "title": a.title or "", "evidence_kind": a.evidence_kind or ""}
        for a in rows[:_MAX_ARTIFACTS]
    ]


def search_prior_work(
    session: Session,
    query: str,
    *,
    workspace_slug: str = "",
    exclude_ticket_id: str = "",
    limit: int = MAX_RESULTS,
) -> list[dict]:
    """Finished tickets whose wording overlaps `query`, best match first.

    Keyword overlap rather than anything cleverer: it is explainable, needs no
    index to maintain, and a wrong hit costs a glance rather than a bad
    decision. Only settled tickets are returned — an in-flight one has not
    established anything yet.
    """
    wanted = _terms(query)
    if not wanted:
        return []

    statement = select(Ticket).where(
        Ticket.state.in_([TicketState.DONE, TicketState.WONT_DO])  # type: ignore[attr-defined]
    )
    if workspace_slug.strip():
        workspace = session.exec(
            select(Workspace).where(Workspace.slug == workspace_slug.strip())
        ).first()
        if not workspace:
            return []
        statement = statement.where(Ticket.workspace_id == workspace.id)

    scored: list[tuple[int, Ticket]] = []
    for ticket in session.exec(statement.limit(_MAX_SCANNED)).all():
        if exclude_ticket_id and ticket.id == exclude_ticket_id:
            continue
        overlap = wanted & _terms(f"{ticket.title} {ticket.description}")
        if overlap:
            scored.append((len(overlap), ticket))

    scored.sort(key=lambda pair: pair[0], reverse=True)
    return [
        {
            "external_id": ticket.external_id,
            "title": ticket.title,
            "state": ticket.state.value,
            "matched_terms": sorted(wanted & _terms(f"{ticket.title} {ticket.description}")),
            "artifacts": _artifact_digest(session, ticket.id),
        }
        for score, ticket in scored[:limit]
    ]
