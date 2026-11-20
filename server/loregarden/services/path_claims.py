"""Which open tickets in a landing tree claim the same files.

Sibling tickets land on one integration branch, and when two of them edit the
same file the second to land conflicts. Over 60 days of live runs (2026-11-19)
`land_ticket.py` conflicted 11 times on the integration branch, and it was the
file that sibling tickets 782 and 801 were both editing while running at the
same time. Neither of their agents could know the other existed.

A ticket's claims come from three places, and only the last is a row of its own:

- **observed**: a path in any of the ticket's runs' `changed_paths_json`;
- **planned**: a path named in the newest plan artifact's `modules_touched`;
- **declared**: a path an agent named mid-run with `loregarden_claim_paths`
  (`DeclaredPathClaim`), the one source nothing else records.

For one path, observed outranks declared, which outranks planned: a fact over an
intent over a prediction. There is nothing to release on landing, and nothing to
drift out of step with the runs: a ticket stops claiming anything the moment it
lands or closes, because only open, unlanded tickets are read.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from loregarden.agents.plan_context import latest_plan
from loregarden.models.domain import (
    AgentRun,
    DeclaredPathClaim,
    Ticket,
    TicketState,
    WorkItemType,
)
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
#: The most paths one `loregarden_claim_paths` call may declare. A claim is a
#: warning to siblings; a list longer than this warns about everything.
MAX_DECLARED_PATHS = 25


class PlanFilesMissingError(ValueError):
    """A plan names no file it will touch, so no sibling can be warned about it."""


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
    ids = [s.id for s in siblings]
    observed = _observed_paths(session, ids)
    declared = _declared_paths(session, ids)
    found: list[SiblingOverlap] = []
    for sibling in siblings:
        claims = _merge_claims(
            _planned_paths(session, sibling),
            declared.get(sibling.id, set()),
            observed.get(sibling.id, set()),
        )
        shared = tuple(
            PathClaim(path, source)
            for path, source in sorted(claims.items())
            if any(paths_overlap(path, mine) for mine in ours)
        )
        if shared:
            found.append(SiblingOverlap(sibling=sibling, shared=shared))
    return found


def ticket_claims(session: Session, ticket: Ticket) -> dict[str, PathClaimSource]:
    """Every path ``ticket`` claims, the strongest source winning for one path."""
    observed = _observed_paths(session, [ticket.id]).get(ticket.id, set())
    declared = _declared_paths(session, [ticket.id]).get(ticket.id, set())
    return _merge_claims(_planned_paths(session, ticket), declared, observed)


def declare_paths(
    session: Session,
    ticket: Ticket,
    paths: Sequence[str],
    *,
    reason: str,
    run_id: str | None,
) -> list[str]:
    """Record ``paths`` as declared by ``ticket``'s run. Returns the paths stored.

    Refuses rather than trims: an agent told "claimed 3 of 5" has no way to know
    which two its siblings will not be warned about. A path the ticket already
    declared is not stored twice.
    """
    cleaned = [_repo_relative(p) for p in paths]
    if not cleaned:
        raise ValueError("claim_paths needs at least one path")
    if len(cleaned) > MAX_DECLARED_PATHS:
        raise ValueError(
            f"claim_paths takes at most {MAX_DECLARED_PATHS} paths, got {len(cleaned)}; "
            "claim the directory (a trailing /) instead of every file in it"
        )
    held = _declared_paths(session, [ticket.id]).get(ticket.id, set())
    new = [p for p in dict.fromkeys(cleaned) if p not in held]
    for path in new:
        session.add(DeclaredPathClaim(ticket_id=ticket.id, run_id=run_id, path=path, reason=reason))
    session.commit()
    return new


def validate_plan_files(content: dict) -> list[str]:
    """The files a plan's `modules_touched` names, or raise `PlanFilesMissingError`.

    Every plan names its files, so a sibling planned beside it can be warned and
    the scheduler can order them before either writes. An entry may carry a note
    after its path (``x.py — the parser``); a count summary names no file.
    """
    raw = content.get("modules_touched")
    hint = (
        "a plan must list the files it will touch as modules_touched: a list of "
        'repo-relative paths, e.g. ["server/loregarden/services/x.py", "client/src/lib/"]'
    )
    try:
        entries = _PLANNED_FILES.validate_python(raw)
    except ValidationError as exc:
        raise PlanFilesMissingError(f"{hint}; got {type(raw).__name__}") from exc
    files = [path for entry in entries if (path := _leading_path(entry))]
    if not files:
        raise PlanFilesMissingError(f"{hint}; none of the {len(entries)} entries names a path")
    return files


def _repo_relative(path: str) -> str:
    cleaned = path.strip().removeprefix("./")
    if not cleaned or cleaned.startswith(("/", "~")) or ".." in cleaned.split("/"):
        raise ValueError(f"claim_paths takes repo-relative paths, got {path!r}")
    return cleaned


def _merge_claims(
    planned: Iterable[str], declared: Iterable[str], observed: Iterable[str]
) -> dict[str, PathClaimSource]:
    claims = dict.fromkeys(planned, PathClaimSource.PLANNED)
    claims.update(dict.fromkeys(declared, PathClaimSource.DECLARED))
    claims.update(dict.fromkeys(observed, PathClaimSource.OBSERVED))
    return claims


def paths_overlap(a: str, b: str) -> bool:
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


def _declared_paths(session: Session, ticket_ids: Sequence[str]) -> dict[str, set[str]]:
    if not ticket_ids:
        return {}
    rows = session.exec(
        select(DeclaredPathClaim.ticket_id, DeclaredPathClaim.path).where(
            col(DeclaredPathClaim.ticket_id).in_(ticket_ids)
        )
    ).all()
    found: dict[str, set[str]] = {}
    for ticket_id, path in rows:
        found.setdefault(ticket_id, set()).add(path)
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
