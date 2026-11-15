"""A landing on an integration branch outlives every move of that branch until the base holds it.

A ticket in a tree lands on its integration branch; the base gets the work only
when the tree is published. Between the two, the branch is the only place the
work is delivered, so a move of the branch that leaves a landing behind loses
it, though the ticket still reads done. `integration/lg-milestone-that-491`
lost lg-milestone-that-1316's landing between 10-20 and 10-30: the tree was
published twice without it, six of its files never reached main, and every
ticket branch cut from the old history (1216, 1420, 1424, 1431) then carried
the last copy and could not take its target.

The writers that move an integration branch, and why each keeps its landings
(`test_landing_survival.py` enumerates them):

- merges and fast-forwards (`git_merge_noco`): the new tip descends from the old;
- the merge turn (`integration_merge_agent._verify`): refuses a HEAD that does
  not contain the branch's tip;
- the reset to the base (`target_branch._fast_forward_superseded`): only when
  the base holds the whole tip, and it keeps that tip under
  `refs/loregarden/settled/` first, so `base_delivery` still finds the
  landings it contains once the branch has left them;
- deletion (`branch_triage_service.delete_branch`, which PR cleanup and the
  branch triage page call): `refuse_dropping_landings` here, with or without
  `force`. Its own commit check counts a commit reachable from *any* branch as
  safe, and a ticket branch cut from the tree is exactly such a branch.

`landings_lost_from_their_branch` reports a landing that is gone anyway: on
neither its branch nor the base, whatever moved it. "On its branch" means the
files it added too, not only its commit in the history: `8ee19bc68` resolved a
merge by deleting 1316's files, and the branch went on carrying the landing as
an ancestor with none of its work (lg-milestone-that-2622). The merge turn now
refuses such a resolution and a hand-made merge must declare it
(`.lefthook/scripts/merge_drops_check.py`); this is what sees one that got by.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from loregarden.models.domain import Ticket, TicketState, Workspace
from loregarden.services.active_workspaces import active_workspaces
from loregarden.services.base_delivery import base_shas, held_landings, holder
from loregarden.services.doctor_landings import base_tips, ever_added, files_dropped_at
from loregarden.services.git_automation_config import base_branch
from loregarden.services.git_merge_noco import rev
from loregarden.services.git_subprocess import run_git
from loregarden.services.invariants import Violation
from loregarden.services.publish_ref import INTEGRATION_PREFIX
from loregarden.services.workspace_paths import resolve_workspace_root
from sqlmodel import Session, col, select

logger = logging.getLogger(__name__)


class LandingsWouldBeLost(ValueError):
    """A move of an integration branch would drop landings the base does not hold."""


def _contains(repo_root: Path, tip: str, sha: str) -> bool:
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


def _undelivered(session: Session, workspace: Workspace, branch: str | None = None) -> list[Ticket]:
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
        t
        for t in _undelivered(session, workspace, branch)
        if _contains(repo_root, tip, t.landed_sha)
    ]
    if new_tip:
        on_it = [t for t in on_it if not _contains(repo_root, new_tip, t.landed_sha)]
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


@dataclass
class _BaseView:
    """What the base holds, read once per workspace and only when a check needs it."""

    repo_root: Path
    name: str
    shas: tuple[str, ...]
    _history: set[str] | None = None

    def ever_had(self, path: str) -> bool:
        if self._history is None:
            self._history = ever_added(self.repo_root, base_tips(self.repo_root, self.name))
        return path in self._history


def _why_lost(repo_root: Path, ticket: Ticket, tip: str, base: _BaseView) -> str:
    """Why ``ticket``'s landing is held by neither ``tip`` nor the base; "" when it is held.

    Raises `RuntimeError` when git cannot tell.
    """
    branch = ticket.landed_branch
    if _contains(repo_root, tip, ticket.landed_sha):
        dropped = files_dropped_at(repo_root, ticket, tip)
        gone = [path for path in dropped if not base.ever_had(path)]
        if not gone:
            return ""
        return (
            f"{ticket.external_id} is done and {branch} still has its landing "
            f"{ticket.landed_sha[:12]} in its history, but a later commit there deleted "
            f"{len(gone)} file(s) it added that {base.name} never had: {', '.join(gone[:5])}"
        )
    if holder(repo_root, ticket.landed_sha, base.shas):
        return ""
    return (
        f"{ticket.external_id} is done, landed as {ticket.landed_sha[:12]} on {branch}, "
        f"but neither {branch} nor {base.name} holds that work any more"
    )


def landings_lost_from_their_branch(session: Session) -> list[Violation]:
    """Done tickets whose landing is on neither their integration branch nor the base.

    Only while the branch exists: see the comment on a missing one below.

    `entered_at` is the ticket's `resolved_at`: the loss happened later, at a
    moment nothing recorded, so a loss after `since` of a ticket resolved
    before it reads as backlog.
    """
    violations: list[Violation] = []
    for workspace in active_workspaces(session):
        repo_root = resolve_workspace_root(workspace)
        if not (repo_root / ".git").exists():
            continue
        done = [t for t in _undelivered(session, workspace) if t.state is TicketState.DONE]
        if not done:
            continue
        name = base_branch(workspace)
        base = _BaseView(repo_root, name, base_shas(repo_root, name))
        tips: dict[str, str] = {}
        for ticket in done:
            branch = ticket.landed_branch
            tip = tips.setdefault(branch, rev(repo_root, f"refs/heads/{branch}"))
            if not tip:
                # Not knowable: with the tree gone, a landing asked on its own reads
                # "not held" whenever a later sibling rewrote its lines, so 804, on
                # main since its September publish, would read lost forever. The
                # deletion that would make this a loss is refused now.
                continue
            try:
                detail = _why_lost(repo_root, ticket, tip, base)
            except RuntimeError as exc:
                # silent-ok: asked again next pass; a check git could not run is not a violation
                logger.warning("Could not tell where %s's landing is: %s", ticket.external_id, exc)
                continue
            if detail:
                violations.append(
                    Violation(
                        subject_id=ticket.id,
                        ticket_id=ticket.id,
                        entered_at=ticket.resolved_at,
                        detail=detail,
                    )
                )
    return violations
