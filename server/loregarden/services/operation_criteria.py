"""Acceptance criteria that are operations on shared state, caught when the spec passes.

lg-milestone-that-782 was a code ticket whose spec also asked for a force-push of
a published integration branch, re-cut worktrees and rewritten `landed_sha` rows.
No implementer can finish such a criterion without a person: four implement
rounds each did the code, stopped at the operation, and asked again. The
criteria were split out by hand after the fourth.

So when the spec stage passes, its criteria are matched against the operations
below. A match does not advance: it becomes a decision with two answers, both
carried out by the control plane rather than by another agent turn:

- split them into a parked sibling ticket (a person owes it), or
- keep them, because they describe code (a feature that pushes, say); the
  waiver is recorded and those criteria are not asked about again.

The patterns are deliberately narrow. Measured against the live database on
2026-10-11 (1175 tickets with criteria), they flag one ticket, the operator
ticket split from 782, and they flag 782's own pre-split criteria. Broader
forms (any `landed_sha`, any "re-cut", "open a PR") flagged code tickets that
build those features. A miss costs rounds, which the decision cap's Park
option bounds; a false alarm costs one click.

The split itself needs `TicketService`, which sits above the orchestration
modules that call this one, so it is installed from above at import time, the
same seam `scheduling` uses.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Callable

from loregarden.models.domain import Artifact, ArtifactKind, Ticket
from loregarden.services.acceptance_criteria import load_criteria
from sqlmodel import Session, select

logger = logging.getLogger(__name__)

#: Each names an operation on shared state, not code that performs one.
OPERATION_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        # force-push / force-update a ref, in any spelling
        r"\bforce[- ]?(?:push|pushe[sd]|pushing|update[sd]?|updating)\b",
        r"\bpush\s+--force\b",
        r"--force-with-lease\b",
        # a branch-protection bypass
        r"\badmin bypass\b",
        # rewriting published history
        r"\bre-?sign(?:ed|ing)?\b.{0,40}\b(?:commits?|history)\b",
        # editing other tickets' landing records through the tools
        r"\blanded_sha\b.{0,120}\bloregarden_update_ticket\b",
        r"\bloregarden_update_ticket\b.{0,120}\blanded_sha\b",
        # re-cutting live worktrees onto a rebuilt branch
        r"\bre-?cut\b.{0,80}\bfrom the new (?:base|tip)\b",
    )
)

SPLIT_OPTION = "Split them into a parked operator ticket, and continue with the code criteria"
KEEP_OPTION = "Keep them here: they describe code, not an operation someone has to run"
OPERATION_DECISION_OPTIONS = (SPLIT_OPTION, KEEP_OPTION)

_KEPT_TITLE = "Operation criteria kept"


#: A negation or a prohibition shortly before a match, within its clause: the
#: criterion rules the operation out rather than asking for it. A regex cannot
#: see every way prose mentions an operation it does not ask for; the decision
#: card's "keep" answer covers what this misses.
_NEGATION = re.compile(
    r"(?:\b(?:no|not|never|without|nor|bans?|forbids?|refuses?|rejects?|disallows?)\b|n't\b)"
    r"[^.;:()]{0,20}$",
    re.IGNORECASE,
)
_CLAUSE_START = re.compile(r".*[.;:(]", re.DOTALL)


def _asks_for(criterion: str, match: re.Match[str]) -> bool:
    """Whether this mention asks for the operation rather than ruling it out.

    lg-external-harness-1026's scope fence read "(no force-push, no ref
    republish, no PR opened by hand …) — it stays a pure code ticket", and was
    held at the spec stage as an operation (2026-10-16).
    """
    before = criterion[: match.start()]
    before = before[_CLAUSE_START.match(before).end() :] if _CLAUSE_START.match(before) else before
    return not _NEGATION.search(before)


def _names_an_operation(criterion: str) -> bool:
    return any(
        _asks_for(criterion, match)
        for pattern in OPERATION_PATTERNS
        for match in pattern.finditer(criterion)
    )


def operation_criteria(criteria: list[str]) -> list[str]:
    """The criteria that ask for an operation on shared state, in their order."""
    return [c for c in criteria if _names_an_operation(c)]


def _kept(session: Session, ticket: Ticket) -> set[str]:
    kept: set[str] = set()
    for artifact in session.exec(
        select(Artifact).where(
            Artifact.ticket_id == ticket.id,
            Artifact.kind == ArtifactKind.CONTEXT,
            Artifact.title == _KEPT_TITLE,
        )
    ).all():
        try:
            rows = json.loads(artifact.content_json or "{}").get("rows", [])
        except json.JSONDecodeError:
            logger.warning("kept-criteria record %s is unreadable", artifact.id)
            continue
        kept.update(str(row.get("v") or "") for row in rows)
    return kept


def pending_operation_criteria(session: Session, ticket: Ticket) -> list[str]:
    """Operation criteria on `ticket` that no one has yet said to keep."""
    kept = _kept(session, ticket)
    return [
        c
        for c in operation_criteria(load_criteria(ticket.acceptance_criteria_json))
        if c not in kept
    ]


def operation_criteria_message(criteria: list[str]) -> str:
    listed = "\n".join(f"- {c}" for c in criteria)
    return (
        "The spec passed, but these acceptance criteria are operations on shared state, "
        "which no implementer can finish without stopping for a person every round:\n"
        f"{listed}\n\n"
        "Split them into a parked ticket a person owns, or keep them if they describe "
        "code. Either answer continues to the next stage; neither runs the spec again."
    )


def keep_operation_criteria(session: Session, ticket: Ticket, criteria: list[str]) -> None:
    """Record that these criteria are code, so the check does not ask again."""
    session.add(
        Artifact(
            ticket_id=ticket.id,
            kind=ArtifactKind.CONTEXT,
            title=_KEPT_TITLE,
            content_json=json.dumps(
                {"title": _KEPT_TITLE, "rows": [{"k": "Kept", "v": c} for c in criteria]}
            ),
        )
    )


#: Moves `criteria` off `ticket` into a parked sibling and returns that ticket.
OperationSplitter = Callable[[Session, Ticket, list[str]], Ticket]

_splitter: OperationSplitter | None = None


def set_operation_splitter(splitter: OperationSplitter | None) -> None:
    """Install the real splitter. Called by `operation_split` on import."""
    global _splitter  # noqa: PLW0603 — one process-wide wiring point, as in `scheduling`
    _splitter = splitter


def split_operation_criteria(session: Session, ticket: Ticket, criteria: list[str]) -> Ticket:
    """Move `criteria` into a parked sibling ticket. Raises when nothing is installed,
    rather than answering a person's choice by doing nothing."""
    if _splitter is None:
        raise RuntimeError(
            "No operation-criteria splitter is installed; "
            "import loregarden.services.operation_split in this entry point"
        )
    return _splitter(session, ticket, criteria)
