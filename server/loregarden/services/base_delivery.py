"""Whether a ticket's landed work is on the base, recorded once the base holds it.

`done` follows from the terminal stage and a landing (`landing.land_before_done`),
and a ticket in a tree lands on the tree's integration branch. The base gets
the work only when `publish_tree` pushes the tree and its PR merges, on a thread
of its own, after `done`. So `done` never meant "on main": 679's tests sat on
an unpublished `integration/lg-run-durability-314` for three weeks with every
ticket on it reading done, and no card said otherwise.

Gating `done` on the base would hold dependents, parents and the board on a PR
merge that takes hours and is a person's call in some workspaces. So `done`
keeps its meaning and this is reported beside it: `Delivery` on the ticket's
detail, from `on_base_sha`, which `record_base_holdings` writes the first time
the base is seen holding the work. It is a fact about history, so it is written
once and never re-checked (`work_landed` says why a later revert does not undo it).

**How the base is seen holding it.** `work_landed.landing_commit`, which sees a
squash publish as well as ancestry, against the remote's base (fetched at most
every `FETCH_EVERY_SECONDS`) and the local one. Asked of a child's own
`landed_sha`, it misses most squash-published children: measured read-only on
the live database 2026-11-03, 84 of 280 landed tickets read "not held" that
way though their trees' tips were on main. Asked of the tree instead it does
not: an integration branch only moves forward, or is reset to the base once
the base holds it (`integration_branch_health`), so when its current tip is
held, every ticket that ever landed on it is. With that rule this module's own
code read 279 of 282 as held in 9.3s; the 3 left are the genuinely unpublished
679 and 1923, and 804, whose tree branch no longer exists to ask.

So per integration branch, the tip and the root's last published tip are asked
once; a ticket with no tree, or a child neither covers, is asked on its own.
A "no" is cached until the base moves, so a pass with nothing new costs one
`for-each-ref`.
"""

from __future__ import annotations

import logging
import threading
import time
from collections import defaultdict
from collections.abc import Sequence
from pathlib import Path

from loregarden.models.domain import Delivery, DeliveryView, Ticket, Workspace
from loregarden.services.active_workspaces import active_workspaces
from loregarden.services.git_automation_config import base_branch
from loregarden.services.git_merge_noco import is_ancestor, rev
from loregarden.services.git_subprocess import run_git
from loregarden.services.publish_ref import INTEGRATION_PREFIX
from loregarden.services.target_branch import subtree_root
from loregarden.services.ticket_workspace_binding import workspace_for_ticket
from loregarden.services.tree_publish_scheduler import pending_publish_card
from loregarden.services.work_landed import landing_commit
from loregarden.services.workspace_paths import resolve_workspace_root
from sqlmodel import Session, col, select

logger = logging.getLogger(__name__)

#: How often a pass may fetch the base from origin. The reconcile tick is 30s,
#: and a PR merged on GitHub can wait five minutes to be noticed.
FETCH_EVERY_SECONDS = 300.0

_GUARD = threading.Lock()
#: repo root → when it last fetched (monotonic).
_FETCHED: dict[str, float] = {}
#: repo root → (the base shas asked against, the shas they did not hold).
_NOT_HELD: dict[str, tuple[tuple[str, ...], set[str]]] = {}


# -- reading the base -----------------------------------------------------------


def base_shas(repo_root: Path, base: str) -> tuple[str, ...]:
    """The remote's base tip and the local one, whichever exist, remote first.

    Fetches the remote's at most every `FETCH_EVERY_SECONDS`. A failed fetch
    is logged and the last-fetched tip is used: a stale base can only delay
    recording that the base holds something, never record it wrongly.
    """
    _fetch(repo_root, base)
    shas: list[str] = []
    for ref in (f"refs/remotes/origin/{base}", f"refs/heads/{base}"):
        sha = rev(repo_root, ref)
        if sha and sha not in shas:
            shas.append(sha)
    return tuple(shas)


def _fetch(repo_root: Path, base: str) -> None:
    key = str(repo_root)
    now = time.monotonic()
    with _GUARD:
        last = _FETCHED.get(key)
        if last is not None and now - last < FETCH_EVERY_SECONDS:
            return
        _FETCHED[key] = now
    fetched = run_git(
        ["fetch", "--quiet", "origin", f"+refs/heads/{base}:refs/remotes/origin/{base}"],
        cwd=str(repo_root),
        check=False,
        capture_output=True,
        text=True,
    )
    if fetched.returncode != 0:
        logger.warning(
            "Could not fetch %s from origin in %s; judging against the last fetch: %s",
            base,
            repo_root,
            (fetched.stderr or fetched.stdout or "").strip(),
        )


def holder(repo_root: Path, sha: str, bases: Sequence[str]) -> str:
    """The base commit holding ``sha``'s work, or "" when no base in ``bases`` does.

    Raises `RuntimeError` when git cannot tell. A "no" is remembered until
    ``bases`` changes, since the answer for a pair of commits never does.
    """
    key = str(repo_root)
    wanted = tuple(bases)
    with _GUARD:
        cached = _NOT_HELD.get(key)
        if cached is not None and cached[0] == wanted and sha in cached[1]:
            return ""
    for base_sha in wanted:
        found = landing_commit(repo_root, sha, base_sha)
        if found:
            return found
    with _GUARD:
        cached = _NOT_HELD.get(key)
        if cached is None or cached[0] != wanted:
            cached = (wanted, set())
            _NOT_HELD[key] = cached
        cached[1].add(sha)
    return ""


# -- recording it ---------------------------------------------------------------


def record_base_holdings(session: Session) -> int:
    """The reconcile step: record `on_base_sha` for each landed ticket the base now holds.

    Returns how many it recorded. A workspace that fails is logged and the rest
    still run; the step raises only if every one did, so the pass names it.
    """
    workspaces = active_workspaces(session)
    recorded = 0
    failures = 0
    for workspace in workspaces:
        try:
            recorded += record_workspace(session, workspace)
        except Exception:  # silent-ok: logged with traceback; one bad repo must not hide the others
            failures += 1
            session.rollback()
            logger.exception("Recording base holdings failed for workspace %s", workspace.slug)
    if workspaces and failures == len(workspaces):
        raise RuntimeError("recording base holdings failed for every workspace")
    return recorded


def _awaiting(session: Session, workspace: Workspace) -> list[Ticket]:
    return list(
        session.exec(
            select(Ticket).where(
                col(Ticket.workspace_id) == workspace.id,
                col(Ticket.landed_sha) != "",
                col(Ticket.on_base_sha) == "",
            )
        ).all()
    )


def record_workspace(session: Session, workspace: Workspace) -> int:
    """`record_base_holdings` for one workspace."""
    repo_root = resolve_workspace_root(workspace)
    if not (repo_root / ".git").exists():
        return 0
    awaiting = _awaiting(session, workspace)
    if not awaiting:
        return 0
    bases = base_shas(repo_root, base_branch(workspace))
    if not bases:
        logger.warning("No %s to judge landings against in %s", base_branch(workspace), repo_root)
        return 0
    held = _held_by_tree(session, repo_root, workspace, awaiting, bases)
    held.update(_held_on_their_own(repo_root, [t for t in awaiting if t.id not in held], bases))
    for ticket in awaiting:
        if ticket.id in held:
            ticket.on_base_sha = held[ticket.id]
            session.add(ticket)
    if held:
        session.commit()
        logger.info("Recorded %d landing(s) on the base in %s", len(held), workspace.slug)
    return len(held)


def _held_on_their_own(
    repo_root: Path, tickets: list[Ticket], bases: Sequence[str]
) -> dict[str, str]:
    """Ticket id → base commit, asking of each ticket's own landing."""
    held: dict[str, str] = {}
    for ticket in tickets:
        try:
            found = holder(repo_root, ticket.landed_sha, bases)
        except RuntimeError as exc:
            # silent-ok: asked again next pass; until then the ticket reads "not on main", which it shows
            logger.warning("Could not tell whether the base holds %s: %s", ticket.external_id, exc)
            continue
        if found:
            held[ticket.id] = found
    return held


def _held_by_tree(
    session: Session,
    repo_root: Path,
    workspace: Workspace,
    awaiting: list[Ticket],
    bases: Sequence[str],
) -> dict[str, str]:
    """Ticket id → base commit, for the tickets their tree's held tips cover."""
    by_branch: dict[str, list[Ticket]] = defaultdict(list)
    for ticket in awaiting:
        if ticket.landed_branch.startswith(INTEGRATION_PREFIX):
            by_branch[ticket.landed_branch].append(ticket)
    held: dict[str, str] = {}
    for branch, tickets in by_branch.items():
        try:
            held.update(_tree_holdings(session, repo_root, workspace, branch, tickets, bases))
        except RuntimeError as exc:
            # silent-ok: each ticket is still asked on its own below, and the next pass asks the tree again
            logger.warning("Could not tell whether the base holds %s: %s", branch, exc)
    return held


def _tree_holdings(
    session: Session,
    repo_root: Path,
    workspace: Workspace,
    branch: str,
    tickets: list[Ticket],
    bases: Sequence[str],
) -> dict[str, str]:
    tip = rev(repo_root, f"refs/heads/{branch}")
    if tip:
        found = holder(repo_root, tip, bases)
        if found:
            # The branch only moves forward or is reset once the base holds it,
            # so everything that ever landed on it is held.
            return {ticket.id: found for ticket in tickets}
    root = _root_of(session, workspace, branch)
    published = root.landed_sha if root and root.landed_branch == branch else ""
    if not published or published == tip:
        return {}
    found = holder(repo_root, published, bases)
    if not found:
        return {}
    return {
        ticket.id: found
        for ticket in tickets
        if ticket.landed_sha == published or is_ancestor(repo_root, ticket.landed_sha, published)
    }


def _root_of(session: Session, workspace: Workspace, branch: str) -> Ticket | None:
    slug = branch.removeprefix(INTEGRATION_PREFIX)
    return session.exec(
        select(Ticket).where(
            col(Ticket.workspace_id) == workspace.id, col(Ticket.external_id) == slug
        )
    ).first()


# -- reading it -----------------------------------------------------------------


def delivery_of(session: Session, ticket: Ticket) -> DeliveryView:
    """Where ``ticket``'s work stands against the base, and the publish card holding it up."""
    if not ticket.landed_sha:
        return DeliveryView(state=Delivery.NOT_LANDED)
    workspace = workspace_for_ticket(session, ticket)
    base = base_branch(workspace) if workspace else ""
    if ticket.on_base_sha:
        return DeliveryView(
            state=Delivery.ON_BASE,
            base=base,
            branch=ticket.landed_branch,
            on_base_sha=ticket.on_base_sha,
        )
    root = subtree_root(session, ticket)
    card = pending_publish_card(session, root)
    return DeliveryView(
        state=Delivery.LANDED_NOT_ON_BASE,
        base=base,
        branch=ticket.landed_branch,
        root_id=root.id,
        root_ref=root.external_id or root.id[:8],
        publish_card_id=card.id if card else "",
        publish_failure=card.impact if card else "",
    )
