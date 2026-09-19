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
"""

from __future__ import annotations

import logging
from pathlib import Path

from loregarden.models.domain import Ticket, Workspace
from loregarden.services.git_branch import validate_branch_name
from loregarden.services.git_subprocess import run_git
from loregarden.services.orchestration_profile import resolve_orchestration_profile
from sqlmodel import Session

logger = logging.getLogger(__name__)

INTEGRATION_PREFIX = "integration/"


class TargetBranchError(RuntimeError):
    """The target branch could not be resolved or created."""


def subtree_root(session: Session, ticket: Ticket) -> Ticket:
    """The topmost ancestor of ``ticket`` — ``ticket`` itself when it has none.

    A dangling ``parent_ticket_id`` (the parent row is gone) ends the walk at
    the last ticket that exists: it is the effective root of what remains.
    """
    current = ticket
    seen = {ticket.id}
    while current.parent_ticket_id:
        parent = session.get(Ticket, current.parent_ticket_id)
        if parent is None or parent.id in seen:
            break
        seen.add(parent.id)
        current = parent
    return current


def integration_branch_for(root: Ticket) -> str:
    """The integration branch a tree rooted at ``root`` lands on."""
    slug = root.external_id.strip() or root.id[:8]
    branch = f"{INTEGRATION_PREFIX}{slug}"
    validate_branch_name(branch)
    return branch


def target_branch_name(session: Session, ticket: Ticket, workspace: Workspace) -> str:
    """The branch ``ticket``'s work lands on, without touching the repository."""
    base = resolve_orchestration_profile(workspace).git.base_branch
    root = subtree_root(session, ticket)
    if root.id == ticket.id:
        return base
    return integration_branch_for(root)


def _branch_exists(repo_root: Path, branch: str) -> bool:
    result = run_git(
        ["rev-parse", "--verify", "--quiet", f"refs/heads/{branch}"],
        cwd=str(repo_root),
        check=False,
        capture_output=True,
        text=True,
    )
    return result.returncode == 0


def ensure_integration_branch(repo_root: Path, branch: str, base_branch: str) -> bool:
    """Create ``branch`` from ``base_branch`` if it is missing. True when created.

    Creation does not check the branch out anywhere, so it is safe while the
    primary checkout and any number of worktrees hold other branches. A base
    that does not exist is an error rather than an empty branch: an integration
    branch with no history would land every ticket as unrelated histories.
    """
    if _branch_exists(repo_root, branch):
        return False
    if not _branch_exists(repo_root, base_branch):
        raise TargetBranchError(
            f"Cannot create {branch!r}: base branch {base_branch!r} does not exist in {repo_root}"
        )
    result = run_git(
        ["branch", branch, base_branch],
        cwd=str(repo_root),
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "git branch failed").strip()
        raise TargetBranchError(f"Cannot create {branch!r} from {base_branch!r}: {detail}")
    logger.info("Created integration branch %s from %s in %s", branch, base_branch, repo_root)
    return True


def resolve_target_branch(
    session: Session, ticket: Ticket, workspace: Workspace, *, repo_root: Path
) -> str:
    """The branch ``ticket`` lands on, existing in ``repo_root`` by the time this returns.

    Idempotent: the second call for the same tree finds the branch and creates
    nothing. ``base_branch`` is never created here — a workspace whose base is
    missing is misconfigured, and that is reported, not repaired.
    """
    base = resolve_orchestration_profile(workspace).git.base_branch
    target = target_branch_name(session, ticket, workspace)
    if target != base:
        ensure_integration_branch(repo_root, target, base)
    return target
