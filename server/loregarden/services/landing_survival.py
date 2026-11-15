"""A landing on an integration branch outlives every move of that branch until the base holds it.

A ticket in a tree lands on its integration branch; the base gets the work only
when the tree is published. Between the two, the branch is the only place the
work is delivered, so a move of the branch that leaves a landing behind loses
it, though the ticket still reads done. `integration/lg-milestone-that-491`
lost lg-milestone-that-1316's landing between 10-20 and 10-30: the tree was
published twice without it, six of its files never reached main, and every
ticket branch cut from the old history (1216, 1420, 1424, 1431) then carried
the last copy and could not take its target.

Every move of an integration branch goes through one owner,
`integration_branch_moves.move_integration_branch`, which calls
`refuse_dropping_landings` here before any move that is not a fast forward,
keeps the tip it leaves under `refs/loregarden/settled/`, and records the move
(`test_landing_survival.py` enumerates the modules that write refs at all).
Its callers: the landing and the tree's creation (`land_ticket`,
`target_branch`), the refresh and the reset to the base (`target_branch`), the
merge turn (`integration_merge_agent`), the root's fold (`publish_tree`), and
deletion (`branch_triage_service.delete_branch`, which also asks here first,
with or without `force`: its own commit check counts a commit reachable from
*any* branch as safe, and a ticket branch cut from the tree is exactly such a
branch).

`landing_survival_checks.landings_lost_from_their_branch` reports a landing
that is gone anyway: on neither its branch nor the base, whatever moved it.
"On its branch" means the files it added too, not only its commit in the
history: `8ee19bc68` resolved a merge by deleting 1316's files, and the branch
went on carrying the landing as an ancestor with none of its work
(lg-milestone-that-2622). The merge turn now refuses such a resolution and a
hand-made merge must declare it (`.lefthook/scripts/merge_drops_check.py`); the
detector is what sees one that got by.
"""

from __future__ import annotations

import logging
from pathlib import Path

from loregarden.models.domain import Ticket, Workspace
from loregarden.services.base_delivery import base_shas, held_landings
from loregarden.services.git_automation_config import base_branch
from loregarden.services.git_merge_noco import rev
from loregarden.services.git_subprocess import run_git
from loregarden.services.publish_ref import INTEGRATION_PREFIX
from loregarden.services.workspace_paths import resolve_workspace_root
from sqlmodel import Session, col, select

logger = logging.getLogger(__name__)


class LandingsWouldBeLost(ValueError):
    """A move of an integration branch would drop landings the base does not hold."""


def contains(repo_root: Path, tip: str, sha: str) -> bool:
    """Whether ``tip``'s history holds ``sha``. Raises when git cannot tell."""
    if sha == tip:
        return True
    result = run_git(
        ["merge-base", "--is-ancestor", sha, tip],
        cwd=str(repo_root),
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode in (0, 1):
        return result.returncode == 0
    raise RuntimeError(
        f"git could not tell whether {tip[:12]} holds {sha[:12]}: "
        f"{(result.stderr or result.stdout).strip()}"
    )


def undelivered(session: Session, workspace: Workspace, branch: str | None = None) -> list[Ticket]:
    """Landings on integration branches with no record of the base holding them."""
    query = select(Ticket).where(
        col(Ticket.workspace_id) == workspace.id,
        col(Ticket.landed_sha) != "",
        col(Ticket.on_base_sha) == "",
    )
    if branch is None:
        query = query.where(col(Ticket.landed_branch).startswith(INTEGRATION_PREFIX))
    else:
        query = query.where(col(Ticket.landed_branch) == branch)
    return list(session.exec(query).all())


def landings_dropped(
    session: Session, workspace: Workspace, branch: str, *, new_tip: str = ""
) -> list[Ticket]:
    """The landings ``branch``'s tip holds that moving it to ``new_tip`` would lose.

    ``new_tip`` empty is a deletion. Only landings the current tip holds count:
    one the branch lost before is not this move's loss, and
    `landings_lost_from_their_branch` reports it. Raises `RuntimeError` when
    git cannot tell, which a caller must treat as "would lose some".
    """
    repo_root = resolve_workspace_root(workspace)
    tip = rev(repo_root, f"refs/heads/{branch}")
    if not tip:
        return []
    on_it = [
        t for t in undelivered(session, workspace, branch) if contains(repo_root, tip, t.landed_sha)
    ]
    if new_tip:
        on_it = [t for t in on_it if not contains(repo_root, new_tip, t.landed_sha)]
    if not on_it:
        return []
    bases = base_shas(repo_root, base_branch(workspace))
    held = held_landings(session, repo_root, workspace, branch, on_it, bases)
    return [t for t in on_it if t.id not in held]


def refuse_dropping_landings(
    session: Session, workspace: Workspace, branch: str, *, new_tip: str = ""
) -> None:
    """Raise `LandingsWouldBeLost` when moving or deleting ``branch`` loses a landing."""
    if not branch.startswith(INTEGRATION_PREFIX):
        return
    base = base_branch(workspace)
    try:
        dropped = landings_dropped(session, workspace, branch, new_tip=new_tip)
    except RuntimeError as exc:
        raise LandingsWouldBeLost(
            f"Could not tell whether {branch} holds landings {base} lacks, so it was kept: {exc}"
        ) from exc
    if not dropped:
        return
    names = ", ".join(t.external_id for t in dropped[:5])
    more = f" and {len(dropped) - 5} more" if len(dropped) > 5 else ""
    raise LandingsWouldBeLost(
        f"{branch} holds the landings of {names}{more}, which {base} does not hold yet, so it "
        f"was kept. Publish the tree first (loregarden_land_ticket on its root), or reopen "
        "those tickets if their work is not wanted."
    )
