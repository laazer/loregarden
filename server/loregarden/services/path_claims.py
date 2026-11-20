"""Which open tickets in a landing tree claim the same files.

Sibling tickets land on one integration branch, and when two of them edit the
same file the second to land conflicts. Over 60 days of live runs (2026-11-19)
`land_ticket.py` conflicted 11 times on the integration branch, and it was the
file that sibling tickets 782 and 801 were both editing while running at the
same time. Neither of their agents could know the other existed.

A claim is derived from data that is already stored, never written as a row of
its own:

- **observed**: a path in any of the ticket's runs' `changed_paths_json`;
- **planned**: a path named in the newest plan artifact's `modules_touched`.

There is nothing to release on landing, and nothing to drift out of step with
the runs: a ticket stops claiming anything the moment it lands or closes,
because only open, unlanded tickets are read.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from loregarden.agents.plan_context import latest_plan
from loregarden.models.domain import AgentRun, Ticket, TicketState, WorkItemType
from loregarden.models.domain.path_claim_enums import PathClaimSource
from loregarden.services.hierarchy_service import descendants_by_root
from loregarden.services.tree_root import subtree_root
from pydantic import TypeAdapter, ValidationError
from sqlmodel import Session, col, select

logger = logging.getLogger(__name__)

_CHANGED_PATHS = TypeAdapter(list[str])
#: States in which a ticket will never land again, so it claims nothing.
_CLOSED_STATES = frozenset({TicketState.DONE, TicketState.WONT_DO})
#: The leading path-shaped token of a `modules_touched` entry. Planners annotate
#: entries ("client/src/lib/approvals.ts — the grouping logic", "x.py (new)"),
#: so only the first token is the path.
_LEADING_PATH = re.compile(r"[\w@./-]+")


#: A plan's `modules_touched` as the plan skill asks for it: a list of paths.
_PLANNED_FILES = TypeAdapter(list[str])
#: The other shape planners write: a count summary (`{"new": 3, "edited": 10}`),
#: which names no file and so predicts nothing.
_PLANNED_COUNTS = TypeAdapter(dict[str, int])


@dataclass(frozen=True)
class PathClaim:
    path: str
    source: PathClaimSource


@dataclass(frozen=True)
class SiblingOverlap:
    """An open ticket in the same landing tree, and its claims that overlap ours."""

    sibling: Ticket
    shared: tuple[PathClaim, ...]


def sibling_overlaps(session: Session, ticket: Ticket) -> list[SiblingOverlap]:
    """Open siblings whose claimed files overlap the files ``ticket`` claims.

    Siblings are the other open, unlanded tickets under the same landing-tree
    root, excluding ``ticket``'s own ancestors and descendants: their work
    contains this ticket's or is contained by it, so it cannot conflict with it.
    Empty when ``ticket`` claims nothing yet: a ticket that has neither planned
    nor changed anything has no files for a sibling to collide with.
    """
    ours = ticket_claims(session, ticket)
    if not ours:
        return []
    siblings = _open_siblings(session, ticket)
    observed = _observed_paths(session, [s.id for s in siblings])
    found: list[SiblingOverlap] = []
    for sibling in siblings:
        claims = _merge_claims(_planned_paths(session, sibling), observed.get(sibling.id, set()))
        shared = tuple(
            PathClaim(path, source)
            for path, source in sorted(claims.items())
            if any(_overlap(path, mine) for mine in ours)
        )
        if shared:
            found.append(SiblingOverlap(sibling=sibling, shared=shared))
    return found


def ticket_claims(session: Session, ticket: Ticket) -> dict[str, PathClaimSource]:
    """Every path ``ticket`` claims, observed winning over planned for one path."""
    observed = _observed_paths(session, [ticket.id]).get(ticket.id, set())
    return _merge_claims(_planned_paths(session, ticket), observed)


def _merge_claims(planned: Iterable[str], observed: Iterable[str]) -> dict[str, PathClaimSource]:
    claims = dict.fromkeys(planned, PathClaimSource.PLANNED)
    claims.update(dict.fromkeys(observed, PathClaimSource.OBSERVED))
    return claims


def _overlap(a: str, b: str) -> bool:
    """Same file, or one claim is a directory (trailing ``/``) holding the other."""
    return a == b or (a.endswith("/") and b.startswith(a)) or (b.endswith("/") and a.startswith(b))


def _open_siblings(session: Session, ticket: Ticket) -> list[Ticket]:
    if ticket.work_item_type is WorkItemType.INITIATIVE:
        # An initiative roots no landing tree, so nothing lands beside it. Any
        # other refusal from `subtree_root` is a hierarchy defect and is raised.
        return []
    root = subtree_root(session, ticket)
    tree = [root, *descendants_by_root(session, [root.id])[root.id]]
    parent_of = {t.id: t.parent_ticket_id for t in tree}
    related = _ancestor_ids(ticket.id, parent_of) | {ticket.id}
    return sorted(
        (
            t
            for t in tree
            if t.id not in related
            and ticket.id not in _ancestor_ids(t.id, parent_of)
            and t.state not in _CLOSED_STATES
            and not t.landed_sha
        ),
        key=lambda t: t.external_id,
    )


def _ancestor_ids(ticket_id: str, parent_of: dict[str, str | None]) -> set[str]:
    found: set[str] = set()
    current = parent_of.get(ticket_id)
    while current and current not in found:
        found.add(current)
        current = parent_of.get(current)
    return found


def _observed_paths(session: Session, ticket_ids: Sequence[str]) -> dict[str, set[str]]:
    if not ticket_ids:
        return {}
    rows = session.exec(
        select(AgentRun.id, AgentRun.ticket_id, AgentRun.changed_paths_json).where(
            col(AgentRun.ticket_id).in_(ticket_ids),
            col(AgentRun.changed_paths_recorded_at).is_not(None),
        )
    ).all()
    found: dict[str, set[str]] = {}
    for run_id, ticket_id, raw in rows:
        try:
            paths = _CHANGED_PATHS.validate_json(raw or "[]")
        except ValidationError:
            # The other runs still answer; this one's files go unclaimed, so a
            # collision through it would be missed. Said here so it is not missed
            # quietly.
            logger.warning("run %s holds unreadable changed_paths_json; skipping", run_id)
            continue
        found.setdefault(ticket_id or "", set()).update(paths)
    return found


def _planned_paths(session: Session, ticket: Ticket) -> set[str]:
    plan = latest_plan(session, ticket)
    if not plan:
        return set()
    raw = plan.get("modules_touched", [])
    try:
        files = _PLANNED_FILES.validate_python(raw)
    except ValidationError:
        if not _is_count_summary(raw):
            logger.warning(
                "plan for %s has a modules_touched of no known shape; its planned files "
                "are unclaimed",
                ticket.external_id,
            )
        return set()
    return {path for entry in files if (path := _leading_path(entry))}


def _is_count_summary(raw: object) -> bool:
    try:
        _PLANNED_COUNTS.validate_python(raw)
    except ValidationError:  # silent-ok: a predicate; failing validation is its "no" answer, and the caller logs that shape
        return False
    return True


def _leading_path(entry: str) -> str:
    """The path an annotated `modules_touched` entry names, or "" when it names none."""
    match = _LEADING_PATH.match(entry.strip())
    token = match.group(0).removeprefix("./").rstrip(".") if match else ""
    # A path has a directory or an extension; a bare word ("tests") is prose.
    return token if "/" in token or "." in token else ""
