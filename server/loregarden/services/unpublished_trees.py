"""Integration branches holding landed work that nothing is taking to the base.

Every landing on a tree schedules its publish (`landing.land_before_done`, 958),
and a publish either succeeds or files a "Publish failed" card. So landed work
the base lacks should always have one of three things behind it: a publish
running, a publish of that very tip (its PR open, waiting on checks or a
merge), or a card in front of a person. A tree with none of them has work that
nothing will deliver.

`integration/lg-run-durability-314` was that, from 2026-10-12 to 2026-11-03:
679's tests landed, the publish never pushed, no card was ever filed, and every
ticket on it read done. It was found by hand during an audit and rescued as
PR #772. Until the card guard (`tree_publish_scheduler.refuse_closing_unpublished`),
closing a card by hand made the same state: the 5 cards people closed between
2026-10-01 and 2026-11-03 all left their work unpublished.

The repair is the publish the tree should have had: `schedule_tree_publish`,
which ends in a pushed tip or a card. A tip that conflicts with the base cannot
be cut, so its publish fails fast and files the card saying so; the work then
waits on a person, and the card cannot be closed until the base holds it.
Each tip is attempted at most once per `RETRY_AFTER_SECONDS`, so a publish that
"succeeds" without recording itself cannot become a push every reconcile tick.

Only workspaces whose tree chain pushes are read: a chain that stops before
the push publishes nothing by design, and its branches are expected to stay put.
A PR closed without merging is not seen here: the root's `landed_sha` still names
the tip it published (`done_ticket_ship` reads PR states; this reads no GitHub).
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from loregarden.models.domain import Ticket, Workspace
from loregarden.services.active_workspaces import active_workspaces
from loregarden.services.base_delivery import base_shas, holder
from loregarden.services.git_automation_config import (
    base_branch,
    enabled_steps,
    resolve_git_automation,
)
from loregarden.services.git_subprocess import run_git
from loregarden.services.invariants import Violation
from loregarden.services.publish_ref import INTEGRATION_PREFIX
from loregarden.services.publish_tree import tree_chain
from loregarden.services.target_branch import subtree_root
from loregarden.services.tree_publish_scheduler import (
    pending_publish_card,
    publish_in_flight,
    schedule_tree_publish,
)
from loregarden.services.workspace_paths import resolve_workspace_root
from sqlmodel import Session, col, select

logger = logging.getLogger(__name__)

#: How long before the same tip of the same tree is published again.
RETRY_AFTER_SECONDS = 3600.0

_GUARD = threading.Lock()
#: (root id, tip) → when this module last scheduled its publish (monotonic).
_ATTEMPTED: dict[tuple[str, str], float] = {}


@dataclass(frozen=True)
class UnpublishedTree:
    root: Ticket
    workspace: Workspace
    branch: str
    tip: str
    #: The tip's commit time: when the unpublished work was last added.
    entered_at: datetime | None


def unpublished_trees(session: Session) -> list[UnpublishedTree]:
    """Every tree whose landed work the base lacks, with nothing taking it there. Read-only.

    A workspace git cannot read is logged and skipped; the next pass reads it again.
    """
    found: list[UnpublishedTree] = []
    for workspace in active_workspaces(session):
        try:
            found.extend(_workspace_trees(session, workspace))
        except RuntimeError as exc:
            # silent-ok: read again on the next reconcile pass; a skipped workspace repairs nothing either
            logger.warning("Could not read %s's integration branches: %s", workspace.slug, exc)
    return found


def _workspace_trees(session: Session, workspace: Workspace) -> list[UnpublishedTree]:
    repo_root = resolve_workspace_root(workspace)
    if not (repo_root / ".git").exists():
        return []
    tips = _integration_tips(repo_root)
    if not tips:
        return []
    roots = _roots(session, workspace, list(tips))
    bases: tuple[str, ...] = ()
    found: list[UnpublishedTree] = []
    for branch, tip in sorted(tips.items()):
        root = roots.get(branch)
        if root is None or not _pushes(workspace, root):
            continue
        if root.landed_branch == branch and root.landed_sha == tip:
            continue  # this tip is published; its PR carries it
        if publish_in_flight(root.id) or pending_publish_card(session, root) is not None:
            continue
        bases = bases or base_shas(repo_root, base_branch(workspace))
        try:
            if not bases or holder(repo_root, tip, bases):
                continue
        except RuntimeError as exc:
            # silent-ok: asked again next pass; one unreadable tip must not hide the other trees
            logger.warning("Could not tell whether the base holds %s: %s", branch, exc)
            continue
        found.append(UnpublishedTree(root, workspace, branch, tip, _commit_time(repo_root, tip)))
    return found


def _pushes(workspace: Workspace, root: Ticket) -> bool:
    return "push" in enabled_steps(tree_chain(resolve_git_automation(workspace, root)))


def _integration_tips(repo_root: Path) -> dict[str, str]:
    result = run_git(
        [
            "for-each-ref",
            "--format=%(refname:short) %(objectname)",
            f"refs/heads/{INTEGRATION_PREFIX}",
        ],
        cwd=str(repo_root),
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"git for-each-ref failed in {repo_root}: {(result.stderr or result.stdout).strip()}"
        )
    tips: dict[str, str] = {}
    for line in result.stdout.splitlines():
        name, _, sha = line.rpartition(" ")
        if name:
            tips[name] = sha
    return tips


def _roots(session: Session, workspace: Workspace, branches: list[str]) -> dict[str, Ticket]:
    """Branch → the tree root it is keyed by. A branch no root here is keyed by is
    not a tree this control plane runs (a hand-made `…-signed-rebuild`, say)."""
    slugs = {branch.removeprefix(INTEGRATION_PREFIX): branch for branch in branches}
    tickets = session.exec(
        select(Ticket).where(
            col(Ticket.workspace_id) == workspace.id, col(Ticket.external_id).in_(list(slugs))
        )
    ).all()
    return {
        slugs[ticket.external_id]: ticket
        for ticket in tickets
        if subtree_root(session, ticket).id == ticket.id
    }


def _commit_time(repo_root: Path, sha: str) -> datetime | None:
    result = run_git(
        ["log", "-1", "--format=%ct", sha],
        cwd=str(repo_root),
        check=False,
        capture_output=True,
        text=True,
    )
    stamp = (result.stdout or "").strip()
    if result.returncode != 0 or not stamp.isdigit():
        logger.warning("Could not read the commit time of %s in %s", sha[:12], repo_root)
        return None  # an undated violation counts as re-entry, which errs towards reporting
    return datetime.fromtimestamp(int(stamp), tz=timezone.utc)


def violations(session: Session) -> list[Violation]:
    """The invariant's detector. `entered_at` is the tip's commit time."""
    return [
        Violation(
            subject_id=tree.root.id,
            ticket_id=tree.root.id,
            entered_at=tree.entered_at,
            detail=(
                f"{tree.branch} at {tree.tip[:12]} holds landed work "
                f"{base_branch(tree.workspace)} does not, with no publish running, none of this "
                "tip, and no publish card"
            ),
        )
        for tree in unpublished_trees(session)
    ]


def publish_trees(session: Session, found: Sequence[Violation]) -> int:
    """The repair: publish each tree still unpublished. Returns how many it scheduled."""
    wanted = {violation.subject_id for violation in found}
    scheduled = 0
    for tree in unpublished_trees(session):
        if tree.root.id not in wanted or not _may_attempt(tree.root.id, tree.tip):
            continue
        logger.warning(
            "Publishing %s for %s: landed work the base lacks had nothing taking it there",
            tree.branch,
            tree.root.external_id,
        )
        schedule_tree_publish(tree.root.id, tree.workspace.id)
        scheduled += 1
    return scheduled


def _may_attempt(root_id: str, tip: str) -> bool:
    now = time.monotonic()
    with _GUARD:
        last = _ATTEMPTED.get((root_id, tip))
        if last is not None and now - last < RETRY_AFTER_SECONDS:
            logger.warning(
                "Not publishing tree %s at %s again yet: attempted %.0fs ago",
                root_id,
                tip[:12],
                now - last,
            )
            return False
        _ATTEMPTED[(root_id, tip)] = now
    return True
