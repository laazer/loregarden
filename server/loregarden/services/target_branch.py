"""Where a ticket's finished work lands.

Every ticket runs on its own branch; this module answers what that branch is
merged *into*. Two answers:

- A ticket inside an orchestrated tree lands on an integration branch the
  orchestrator owns, one per tree, named after the tree's root. Siblings that
  depend on each other see one another's work there without anything reaching
  ``main``, and the tree goes to ``main`` once, as a whole.
- A top-level ticket — including the root of a tree — lands on the workspace's
  ``base_branch``.

Neither stacking (cut ticket N+1 from ticket N) nor landing every ticket on
``main`` was chosen. A stack breaks when N gets a rework round after N+1 has
branched, and independent siblings cannot run in parallel from one. Landing on
``main`` per ticket needs either a PR each — a person per ticket, which is what
running a milestone unattended exists to remove — or ``gh pr merge --auto``,
which merges immediately in a repository without branch protection. A local
merge into a branch the orchestrator owns needs neither.

The root keys the branch rather than the nearest parent so a feature's tasks
and the feature's own siblings share one branch; the tree lands in dependency
order, so nothing on it is ever ahead of what its dependents need.

An initiative is never a root. It spans workspaces, never orchestrates, and
never completes the way a tree does, so a branch named after it would collect
every milestone's work in each repository and publish none of it. The walk
stops below it: a milestone keeps its own branch whether or not it is filed
under an initiative.
"""

from __future__ import annotations

import logging
from pathlib import Path

from loregarden.models.domain import Ticket, Workspace
from loregarden.services.branch_names import validate_branch_name
from loregarden.services.git_automation_config import base_branch
from loregarden.services.git_merge_noco import (
    BranchTips,
    MoveRef,
    describe_signing,
    merge_tips,
    read_branch_tips,
)
from loregarden.services.integration_branch_moves import mover
from loregarden.services.integration_conflict_card import (
    clear_integration_conflict,
    report_integration_conflict,
)
from loregarden.services.integration_merge_agent import merge_base_by_agent
from loregarden.services.publish_ref import INTEGRATION_PREFIX
from loregarden.services.tree_root import TargetBranchError, refuse_initiative, subtree_root
from loregarden.services.work_landed import landing_commit
from pydantic import BaseModel, ConfigDict
from sqlmodel import Session

logger = logging.getLogger(__name__)


class IntegrationConflictError(TargetBranchError):
    """An integration branch exists but cannot take its base: the merge conflicts.

    Its own type because it is the one failure a caller can still work around:
    the branch is there, only stale, so a turn that does not need the base's
    newest commits (a chat turn) can run on it as it is.
    """

    def __init__(self, branch: str, base: str, files: tuple[str, ...]) -> None:
        super().__init__(f"{branch!r} cannot take {base!r}: merge conflicts in {', '.join(files)}")
        self.branch = branch
        self.base = base
        self.files = files
        #: Why the automatic merge turn did not resolve it, when one was tried.
        self.automatic = ""

    def __str__(self) -> str:
        if not self.automatic:
            return super().__str__()
        return f"{super().__str__()} (automatic merge: {self.automatic})"


def integration_branch_for(root: Ticket) -> str:
    """The integration branch a tree rooted at ``root`` lands on."""
    refuse_initiative(root)
    slug = root.external_id.strip() or root.id[:8]
    branch = f"{INTEGRATION_PREFIX}{slug}"
    validate_branch_name(branch)
    return branch


class LandingTarget(BaseModel):
    """Where a ticket's work lands, at both widths one walk already answers.

    ``root`` is the tree root that keys ``branch``, and is ``None`` exactly when
    the ticket is its own root — which is also the only case where ``branch`` is
    the workspace's base branch. It is never the ticket itself, so a caller that
    has a root has a different, existing ticket to name.
    """

    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    branch: str
    root: Ticket | None


def landing_target(session: Session, ticket: Ticket, workspace: Workspace) -> LandingTarget:
    """The branch ``ticket``'s work lands on and the root that keys it.

    One derivation exposed at two widths: `target_branch_name` is this function
    read at the narrower one. Callers that want only the string keep getting
    only the string; a caller that would otherwise re-derive the root from the
    branch name reads it here instead, which the branch string cannot answer
    (`integration_branch_for` slugs a root with no external id to eight hex
    characters no route resolves).
    """
    base = base_branch(workspace)
    root = subtree_root(session, ticket)
    if root.id == ticket.id:
        return LandingTarget(branch=base, root=None)
    return LandingTarget(branch=integration_branch_for(root), root=root)


def target_branch_name(session: Session, ticket: Ticket, workspace: Workspace) -> str:
    """The branch ``ticket``'s work lands on, without touching the repository."""
    return landing_target(session, ticket, workspace).branch


def ensure_integration_branch(
    repo_root: Path, branch: str, base_branch: str, *, move: MoveRef
) -> BranchTips:
    """Create ``branch`` from ``base_branch`` if it is missing.

    Returns both tips as they stand afterwards — read again after a creation,
    since creating is the one step here that moves a ref — so a caller about to
    merge can use them rather than ask git twice for the same two refs.

    Creation does not check the branch out anywhere, so it is safe while the
    primary checkout and any number of worktrees hold other branches. A base
    that does not exist is an error rather than an empty branch: an integration
    branch with no history would land every ticket as unrelated histories.
    """
    tips = read_branch_tips(repo_root, branch, base_branch)
    if tips.of(branch):
        return tips
    if not tips.of(base_branch):
        reason = f": {tips.error}" if tips.error else ""
        raise TargetBranchError(
            f"Cannot create {branch!r}: base branch {base_branch!r} does not exist "
            f"in {repo_root}{reason}"
        )
    refused = move(branch, tips.of(base_branch), "")
    if refused:
        raise TargetBranchError(f"Cannot create {branch!r} from {base_branch!r}: {refused}")
    logger.info("Created integration branch %s from %s in %s", branch, base_branch, repo_root)
    return read_branch_tips(repo_root, branch, base_branch)


def refresh_integration_branch(
    repo_root: Path, branch: str, base_branch: str, *, move: MoveRef
) -> bool:
    """Bring ``branch`` up to ``base_branch``. True when ``branch`` moved.

    A tree lands on its integration branch for as long as it runs, while the
    base keeps moving — other trees publish, people merge by hand. A ticket
    cut from an integration branch that never takes the base in builds against
    a base weeks old, and a prerequisite that reached ``main`` through another
    tree's publish is invisible to it (770). Done without a checkout, so it is
    safe from any working tree; a conflict is raised, because a ticket about to
    be cut from a branch that cannot take its base is not a ticket to start.

    The exception is a branch whose work the base already holds, typically
    through a squash merge: its conflict is with its own work, rewritten. That
    branch is moved to the base tip instead (`_fast_forward_superseded`).
    """
    return _refresh_from_tips(
        repo_root,
        read_branch_tips(repo_root, branch, base_branch),
        branch,
        base_branch,
        move=move,
    )


def _refresh_from_tips(
    repo_root: Path, tips: BranchTips, branch: str, base_branch: str, *, move: MoveRef
) -> bool:
    outcome = merge_tips(
        repo_root,
        tips,
        target=branch,
        source=base_branch,
        subject=f"Refresh {branch} from {base_branch}",
        move=move,
    )
    if outcome.ok:
        if not outcome.already_contained:
            logger.info(
                "Refreshed %s from %s as %s (%s)",
                branch,
                base_branch,
                outcome.sha[:12],
                describe_signing(outcome.signed),
            )
        return not outcome.already_contained
    if outcome.conflicted:
        if _fast_forward_superseded(repo_root, tips, branch, base_branch, move=move):
            return True
        raise IntegrationConflictError(branch, base_branch, outcome.conflicted_files)
    raise TargetBranchError(f"Cannot refresh {branch!r} from {base_branch!r}: {outcome.detail}")


def _fast_forward_superseded(
    repo_root: Path, tips: BranchTips, branch: str, base_branch: str, *, move: MoveRef
) -> bool:
    """Move ``branch`` to the base tip when the base already holds all its work.

    False, touching nothing, when the branch has anything the base lacks: a
    branch with unique work is never reset. The move is a compare-and-swap on
    the tip just read, so a branch that gained a commit meanwhile is refused.
    """
    branch_sha, base_sha = tips.of(branch), tips.of(base_branch)
    holder = landing_commit(repo_root, branch_sha, base_sha)
    if not holder:
        return False
    # The owner keeps the tip it leaves under the settled refs before moving.
    detail = move(branch, base_sha, branch_sha)
    if detail:
        raise TargetBranchError(
            f"{branch!r} is already in {base_branch!r} (as {holder[:12]}) but could not be "
            f"moved to it: {detail}"
        )
    logger.warning(
        "Reset %s from %s to %s tip %s: its work is already in %s as %s",
        branch,
        branch_sha[:12],
        base_branch,
        base_sha[:12],
        base_branch,
        holder[:12],
    )
    return True


def resolve_target_branch(
    session: Session,
    ticket: Ticket,
    workspace: Workspace,
    *,
    repo_root: Path,
    merge_conflicts: bool = True,
) -> str:
    """The branch ``ticket`` lands on, existing and current in ``repo_root`` on return.

    Idempotent: the second call for the same tree finds the branch, creates
    nothing, and merges nothing unless the base moved. ``base_branch`` is never
    created here — a workspace whose base is missing is misconfigured, and
    that is reported, not repaired.

    A branch that cannot take its base gets an agent turn to merge it
    (`integration_merge_agent`), which blocks this call while it runs; only
    when that is off, spent or fails is the conflict raised and put in front of
    a person. ``merge_conflicts=False`` skips the turn, for a caller that can
    run on the stale branch rather than wait (a chat turn).
    """
    base = base_branch(workspace)
    target = target_branch_name(session, ticket, workspace)
    if target != base:
        root = subtree_root(session, ticket)
        try:
            _refresh_or_merge(
                session, workspace, root, repo_root, target, base, merge_conflicts=merge_conflicts
            )
        except IntegrationConflictError as exc:
            # Whoever hits it first files the card; raising alone left the only
            # record in one failed run or chat reply.
            report_integration_conflict(
                session,
                root,
                branch=exc.branch,
                base=exc.base,
                files=exc.files,
                automatic=exc.automatic,
            )
            raise
        # The branch takes its base now; a card filed earlier no longer holds.
        clear_integration_conflict(session, root, branch=target, base=base)
    return target


def _refresh_or_merge(
    session: Session,
    workspace: Workspace,
    root: Ticket,
    repo_root: Path,
    target: str,
    base: str,
    *,
    merge_conflicts: bool,
) -> None:
    move = mover(
        session,
        workspace,
        writer="target_branch.refresh",
        why=f"bring {target} up to {base} before a ticket is cut from it",
        repo_root=repo_root,
    )
    try:
        # One reading of both refs decides creation and the refresh alike.
        create = mover(
            session,
            workspace,
            writer="target_branch.create",
            why=f"create {target} from {base} for the tree's first ticket",
            repo_root=repo_root,
        )
        tips = ensure_integration_branch(repo_root, target, base, move=create)
        _refresh_from_tips(repo_root, tips, target, base, move=move)
        return
    except IntegrationConflictError as exc:
        if not merge_conflicts:
            raise
        merged = merge_base_by_agent(
            session, workspace, root, repo_root=repo_root, branch=target, base=base, files=exc.files
        )
        if not merged.ok:
            exc.automatic = merged.detail
            raise
    # Merged: the refresh is a no-op now, unless the base moved again meanwhile,
    # which raises for the next dispatch rather than looping here.
    _refresh_from_tips(
        repo_root, read_branch_tips(repo_root, target, base), target, base, move=move
    )
