"""Let a tree root's own branch carry a fix to its integration branch.

A tree's pull request is on `integration/<root>`, but every turn on the root
runs in the root's own worktree, on the root's own branch, and landing skips a
root (its target is the base). So a repair turn on the root, the move the CI
auto-fix makes for any other ticket, used to commit somewhere the pull request
never saw. Two steps close that, one on each side of the turn:

1. **Before** (`take_integration_branch`): the root's branch takes the
   integration branch, so the turn starts from exactly what CI ran on. Done in
   the checkout holding the branch when there is one, since moving a
   checked-out ref underneath its working tree would show every file as
   reverted; otherwise without a checkout.
2. **After** (`fold_root_branch`, from `publish_tree`): commits the turn made
   on the root's branch go onto the integration branch before it is pushed,
   so the fix reaches the PR and CI runs on it.

Step 1 records where the root's branch stood once it had taken the
integration branch, as a ref under `refs/loregarden/tree-fix-base/`, and step
2 folds only when the branch has moved past it. Kept in git rather than the
database because it is a fact about two refs, read beside them. Without it,
the fold would also publish whatever the root's branch held before (commits
from the root's own stages, built against the base, which the tree was never
built with), and a turn that committed nothing would still move the PR.

Routing the fix to the child whose work broke CI was considered and not done:
CI reports a failing check, not a culprit, so the child would be a guess, and
its landing would still need the root to publish again.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from loregarden.models.domain import Ticket
from loregarden.services.branch_names import resolve_ticket_branch
from loregarden.services.git_merge_noco import (
    MergeOutcome,
    advance_tips,
    is_ancestor,
    read_branch_tips,
    rev,
)
from loregarden.services.git_subprocess import run_git

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SyncOutcome:
    """Whether the root's branch now contains the integration branch; ``detail`` says why not."""

    ok: bool
    detail: str = ""


FIX_BASE_PREFIX = "refs/loregarden/tree-fix-base/"


def _fix_base_ref(own: str) -> str:
    return f"{FIX_BASE_PREFIX}{own}"


class _CheckoutLookupError(RuntimeError):
    """`git worktree list` failed, so whether the branch is checked out is unknown."""


def _git(cwd: Path, *args: str):
    return run_git(list(args), cwd=str(cwd), check=False, capture_output=True, text=True)


def _words(result, fallback: str) -> str:
    return (result.stderr or result.stdout or fallback).strip()


def _checkout_holding(repo_root: Path, branch: str) -> Path | None:
    """The working tree that has ``branch`` checked out, or None when none does."""
    listed = _git(repo_root, "worktree", "list", "--porcelain")
    if listed.returncode != 0:
        # "Unknown" must not read as "nobody": moving the ref under a checkout
        # would leave it showing every merged file as a local revert.
        raise _CheckoutLookupError(_words(listed, "git worktree list failed"))
    path: Path | None = None
    for line in listed.stdout.splitlines():
        if line.startswith("worktree "):
            path = Path(line.removeprefix("worktree "))
        elif line == f"branch refs/heads/{branch}" and path is not None:
            return path
    return None


def _merge_in_checkout(checkout: Path, own: str, integration: str) -> SyncOutcome:
    status = _git(checkout, "status", "--porcelain", "--untracked-files=all")
    if status.returncode != 0:
        return SyncOutcome(False, f"cannot read {checkout}: {_words(status, 'git status failed')}")
    if status.stdout.strip():
        return SyncOutcome(
            False,
            f"{own} is checked out at {checkout} with uncommitted changes; "
            f"it cannot take {integration} until they are committed or discarded",
        )
    merged = _git(checkout, "merge", "--no-edit", integration)
    if merged.returncode == 0:
        return SyncOutcome(True)
    detail = _words(merged, "git merge failed")
    aborted = _git(checkout, "merge", "--abort")
    if aborted.returncode != 0:
        detail += f"; and `git merge --abort` failed: {_words(aborted, 'no output')}"
    return SyncOutcome(False, f"{own} cannot take {integration} at {checkout}: {detail}")


def take_integration_branch(repo_root: Path, *, own: str, integration: str) -> SyncOutcome:
    """Bring the root's branch ``own`` up to ``integration`` and mark where it stands.

    Nothing is pushed. The mark is what `fold_root_branch` folds past.
    """
    synced = _take(repo_root, own=own, integration=integration)
    if not synced.ok:
        return synced
    tip = rev(repo_root, f"refs/heads/{own}")
    marked = _git(repo_root, "update-ref", _fix_base_ref(own), tip)
    if not tip or marked.returncode != 0:
        return SyncOutcome(
            False, f"cannot mark {own} at {tip[:12] or '?'}: {_words(marked, 'no tip')}"
        )
    return SyncOutcome(True)


def _take(repo_root: Path, *, own: str, integration: str) -> SyncOutcome:
    tips = read_branch_tips(repo_root, own, integration)
    if not tips.of(integration):
        return SyncOutcome(False, tips.error or f"branch {integration!r} does not exist")
    if not tips.of(own):
        created = _git(repo_root, "branch", own, tips.of(integration))
        if created.returncode != 0:
            return SyncOutcome(False, f"cannot create {own!r}: {_words(created, 'git branch')}")
        return SyncOutcome(True)
    if is_ancestor(repo_root, tips.of(integration), tips.of(own)):
        return SyncOutcome(True)
    try:
        checkout = _checkout_holding(repo_root, own)
    except _CheckoutLookupError as exc:
        return SyncOutcome(False, f"cannot tell whether {own} is checked out: {exc}")
    if checkout is not None:
        return _merge_in_checkout(checkout, own, integration)
    outcome = advance_tips(
        repo_root, tips, target=own, source=integration, subject=f"Take {integration} into {own}"
    )
    if outcome.ok:
        return SyncOutcome(True)
    return SyncOutcome(False, f"{own} cannot take {integration}: {outcome.detail}")


def fold_root_branch(repo_root: Path, root: Ticket, integration: str) -> MergeOutcome | None:
    """Move ``integration`` onto the commits a turn made on the root's branch.

    None when there is nothing to fold: no turn took ``integration`` into the
    root's branch, or the branch has not moved since it did. The mark is
    cleared once the fold lands or there is nothing past it, and kept when the
    fold fails, so the next publish tries again.
    """
    own = resolve_ticket_branch(root)
    base = rev(repo_root, _fix_base_ref(own))
    if not base:
        return None
    tips = read_branch_tips(repo_root, own, integration)
    own_tip = tips.of(own)
    if own_tip == base or not is_ancestor(repo_root, base, own_tip):
        # Nothing committed past the mark, or the branch was rewritten under it.
        _forget(repo_root, own)
        return None
    outcome = advance_tips(
        repo_root,
        tips,
        target=integration,
        source=own,
        subject=f"Fold {own} into {integration}",
    )
    if outcome.ok:
        _forget(repo_root, own)
        logger.info("Folded %s into %s at %s", own, integration, outcome.sha[:12])
    return outcome


def _forget(repo_root: Path, own: str) -> None:
    dropped = _git(repo_root, "update-ref", "-d", _fix_base_ref(own))
    if dropped.returncode != 0:
        # A mark left behind makes a later publish fold the root's later
        # commits too: say so rather than leave it unexplained.
        logger.warning(
            "Could not clear %s: %s", _fix_base_ref(own), _words(dropped, "git update-ref -d")
        )
