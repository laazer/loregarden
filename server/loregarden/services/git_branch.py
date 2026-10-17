"""Git branch helpers for ticket-scoped agent runs."""

from __future__ import annotations

import logging
import re
import subprocess
from collections.abc import Iterable
from pathlib import Path

from loregarden.models.domain import (
    BaxterChatSession,
    BranchHolder,
    PrimaryCheckoutUse,
    Ticket,
)
from loregarden.services.branch_takes_target import MergeTurnSeat, bring_tree_along, take_target
from loregarden.services.git_merge_noco import is_ancestor
from loregarden.services.git_subprocess import run_git
from loregarden.services.git_trees import is_main_working_tree, main_working_tree
from loregarden.services.primary_checkout import require_clean_tree
from loregarden.services.worktree_snapshot import read_tree

logger = logging.getLogger(__name__)

_BRANCH_RE = re.compile(r"^[A-Za-z0-9._/-]+$")
_SLUG_RE = re.compile(r"[^a-z0-9]+")
_WORKTREE_LOCK_RE = re.compile(
    r"already used by worktree at ['\"]?([^'\"]+)['\"]?",
    re.IGNORECASE,
)


def _slugify(text: str) -> str:
    slug = _SLUG_RE.sub("-", text.lower()).strip("-")
    return slug[:48]


def default_ticket_branch(ticket: Ticket) -> str:
    slug = ticket.external_id.strip() or ticket.id[:8]
    prefix = _slugify(ticket.milestone) or "loregarden"
    return f"{prefix}/{slug}"


def chat_session_branch(chat_session: BaxterChatSession) -> str:
    """The branch a Home chat thread's acting turns commit to.

    Derived from the thread's own id rather than its title: a title is derived
    from the first message and the operator can rename it, and a branch that
    moves when a conversation is renamed is a branch that loses its commits.
    The title only decorates, so a thread is recognisable in `git branch`.
    """
    slug = _slugify(chat_session.title)
    stem = f"{slug}-{chat_session.id[:8]}" if slug else chat_session.id[:8]
    return f"chat/{stem}"


def resolve_ticket_branch(ticket: Ticket) -> str:
    branch = ticket.branch.strip()
    return branch or default_ticket_branch(ticket)


def validate_branch_name(branch: str) -> None:
    if not branch or not _BRANCH_RE.match(branch):
        raise ValueError(f"Invalid branch name: {branch!r}")


def _process_text(exc: subprocess.CalledProcessError) -> str:
    stderr = (exc.stderr or "") if isinstance(exc.stderr, str) else ""
    stdout = (exc.stdout or "") if isinstance(exc.stdout, str) else ""
    return (stderr or stdout or str(exc)).strip()


def _worktree_lock_path(detail: str) -> str | None:
    match = _WORKTREE_LOCK_RE.search(detail)
    return match.group(1).strip() if match else None


def _classify_holder(locked_at: str, main_tree: Path | None) -> BranchHolder:
    """Which kind of tree `locked_at` is, decided once from one derived fact.

    An unresolvable `locked_at` classifies as `MAIN_WORKING_TREE` rather than
    `UNIDENTIFIED`: nothing is removed either way, but `UNIDENTIFIED`'s wording
    claims git could not identify the main working tree, which is only true
    when `main_working_tree` itself could not answer.
    """
    if main_tree is None:
        return BranchHolder.UNIDENTIFIED
    if is_main_working_tree(locked_at, main_tree=main_tree):
        return BranchHolder.MAIN_WORKING_TREE
    return BranchHolder.LINKED_WORKTREE


def _free_the_branch_with(locked_at: str, *, start_point: str) -> str:
    """The remedy for a tree that must not be removed, and why it is that one."""
    if start_point:
        return (
            f"Free the branch with:\n  git -C {locked_at} switch {start_point}\nthen retry the run."
        )
    return (
        f"Free the branch with:\n"
        f"  git -C {locked_at} switch --detach\n"
        f"then retry the run. Detaching frees the branch without choosing another "
        f"one for you."
    )


def _held_branch_message(
    branch: str, locked_at: str, *, holder: BranchHolder, start_point: str
) -> str:
    """What the operator reads when something else is holding `branch`.

    Three shapes, one per `BranchHolder`, and each ends in a command that can
    succeed against the tree named on its first line (UX-1, UX-2). The string
    `worktree remove --force` appears in the linked-worktree shape only: aimed
    at a main working tree git exits 128 however the operator runs it, so
    printing it there hands over a remedy that cannot work (AC2).
    """
    if holder is BranchHolder.LINKED_WORKTREE:
        return (
            f"Branch {branch!r} is already checked out in another worktree at "
            f"{locked_at}. Git refuses a second checkout (exit 128).\n\n"
            f"Free it with:\n"
            f"  git worktree remove --force {locked_at}\n"
            f"then retry the run. Stale Claude/Cursor scratchpad worktrees are a "
            f"common cause."
        )
    if holder is BranchHolder.MAIN_WORKING_TREE:
        return (
            f"Branch {branch!r} is checked out in this repository's main working tree "
            f"at {locked_at}. Git refuses a second checkout (exit 128), and that tree "
            f"must not be removed.\n\n"
            f"{_free_the_branch_with(locked_at, start_point=start_point)}"
        )
    return (
        f"Branch {branch!r} is checked out at {locked_at}, and git could not identify "
        f"this repository's main working tree, so removing {locked_at} cannot be shown "
        f"to be safe.\n\n"
        f"Free the branch with:\n"
        f"  git -C {locked_at} switch --detach\n"
        f"then retry the run. Detaching frees the branch whether or not that tree is "
        f"the main one."
    )


def _checkout_failure_message(
    branch: str,
    exc: subprocess.CalledProcessError,
    *,
    repair_note: str = "",
    holder: BranchHolder = BranchHolder.LINKED_WORKTREE,
    start_point: str = "",
) -> str:
    detail = _process_text(exc)
    locked_at = _worktree_lock_path(detail)
    if locked_at:
        prefix = repair_note.strip() + "\n\n" if repair_note.strip() else ""
        body = _held_branch_message(branch, locked_at, holder=holder, start_point=start_point)
        return f"{prefix}{body}"
    return detail or f"git checkout -B {branch!r} failed ({exc})"


def _try_free_worktree_lock(
    repo_root: Path, branch: str, locked_at: str, *, holder: BranchHolder
) -> tuple[bool, str]:
    """Try to remove a linked worktree holding `branch`.

    Returns ``(repaired, note)``. ``repaired`` is True only when the lock was
    cleared and checkout should be retried. Only a `LINKED_WORKTREE` is removed:
    the decision is about the tree holding the branch, never about which tree
    the dispatch was standing in, which is what `repo_root` answers now that it
    is routinely the ticket's own worktree (lg-milestone-that-916).
    """
    if holder is not BranchHolder.LINKED_WORKTREE:
        logger.warning(
            "Refusing to remove primary checkout %s while switching to %s",
            locked_at,
            branch,
        )
        return False, ""

    result = run_git(
        ["worktree", "remove", "--force", locked_at],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        detail = ((result.stderr or result.stdout) or "worktree remove failed").strip()
        logger.warning(
            "Self-repair could not remove worktree %s locking %s: %s",
            locked_at,
            branch,
            detail,
        )
        return False, f"Tried to remove locking worktree {locked_at} but failed: {detail}"

    run_git(
        # silent-ok: bookkeeping after the removal above already freed the branch
        ["worktree", "prune"],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=False,
    )
    note = f"Self-repaired: removed locking worktree {locked_at} so {branch!r} can check out."
    logger.warning(note)
    return True, note


def _branch_exists(repo_root: Path, branch: str) -> bool:
    result = run_git(
        ["rev-parse", "--verify", "--quiet", f"refs/heads/{branch}"],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=False,
    )
    return result.returncode == 0


def _checkout_branch(
    repo_root: Path, branch: str, *, start_point: str, seat: MergeTurnSeat | None = None
) -> None:
    """Check ``branch`` out, creating it from ``start_point`` only if it is missing.

    This was ``checkout -B``, which *resets* an existing branch to HEAD. On any
    re-dispatch — a retry, a rework round after another ticket had moved HEAD, a
    requeue — the ticket's earlier commits were left unreachable and the tree
    looked like a first attempt (lg-milestone-that-772).

    An existing branch is then brought up to ``start_point`` when one is given
    and the tree is clean, so a prerequisite that landed since the branch was
    cut reaches the ticket (lg-milestone-that-769). A dirty tree is left alone
    — merging over uncommitted work is how it gets lost — and said so. A
    conflict goes to `branch_takes_target` before it blocks the ticket.
    """
    if not _branch_exists(repo_root, branch):
        if start_point and not _branch_exists(repo_root, start_point):
            # A repository with no commits yet has no base branch to cut from.
            # HEAD is the only thing there is; said out loud because on any
            # other repository this means the workspace's base_branch is wrong.
            logger.warning(
                "Start point %r does not exist in %s; cutting %s from HEAD",
                start_point,
                repo_root,
                branch,
            )
            start_point = ""
        args = (
            ["checkout", "-b", branch, start_point] if start_point else ["checkout", "-b", branch]
        )
        run_git(args, cwd=repo_root, capture_output=True, text=True, check=True)
        return
    run_git(["checkout", branch], cwd=repo_root, capture_output=True, text=True, check=True)
    if start_point:
        _refresh_onto(repo_root, branch, start_point, seat=seat)


def _merge_in(repo_root: Path, start_point: str) -> str:
    """Merge ``start_point`` into the checked-out branch; "" on success, else git's words."""
    merged = run_git(
        ["merge", "--no-edit", start_point],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=False,
    )
    if merged.returncode == 0:
        return ""
    # silent-ok: cleanup on an already-failing path; the caller raises with the
    # merge's own output, and a failed abort leaves conflict markers the next
    # `status --porcelain` refuses to merge over.
    run_git(["merge", "--abort"], cwd=repo_root, capture_output=True, text=True, check=False)
    return (merged.stdout or merged.stderr or "git merge failed").strip()


def _refresh_onto(
    repo_root: Path, branch: str, start_point: str, *, seat: MergeTurnSeat | None = None
) -> None:
    dirty = run_git(
        ["status", "--porcelain"], cwd=repo_root, capture_output=True, text=True, check=True
    )
    if dirty.stdout.strip():
        logger.warning(
            "Not refreshing %s from %s: %s has uncommitted changes", branch, start_point, repo_root
        )
        return
    detail = _merge_in(repo_root, start_point)
    if not detail:
        return
    taken = take_target(repo_root, branch, start_point, seat=seat)
    if taken.ok:
        bring_tree_along(repo_root, taken)
        detail = _merge_in(repo_root, start_point)
        if not detail:
            return
    else:
        detail = f"{detail}\n{taken.detail}"
    raise ValueError(f"Branch {branch!r} could not take {start_point!r} before starting: {detail}")


def ensure_ticket_branch(
    repo_root: Path,
    ticket: Ticket,
    *,
    start_point: str = "",
    ignoring: Iterable[str] = (),
    seat: MergeTurnSeat | None = None,
) -> str:
    """Checkout or create the branch a ticket should run on.

    ``start_point`` is where a *missing* branch is cut from; empty means HEAD,
    which is where the callers still cut from until the target branch reaches
    them (lg-milestone-that-769). An existing branch is never reset: it takes
    ``start_point`` by merge, and is moved onto it only when ``start_point``
    already holds everything it adds (`branch_takes_target`). ``seat`` lets a
    conflict get an agent's merge turn; without one, a conflict still raises.

    If another linked worktree holds the branch — common with stale Claude
    scratchpads — remove that worktree once and retry. Gate autofix never sees
    this failure because it happens before any agent starts.

    Returns early, having asked git exactly one question, when the tree is
    already on the branch and ``start_point`` is already an ancestor of its
    HEAD: nothing landed on the target since, so the checkout cannot move the
    tree and the merge is a no-op (lg-milestone-that-918).

    Which tree is this repository's *main* working tree is derived once per
    call, from git, and held for the rest of it: both the removal guard and the
    `PrimaryCheckoutUse` the refusal names read that one value, and derived
    separately they can disagree invisibly (lg-milestone-that-916). That is one
    `rev-parse` per dispatch, and `ensure_ticket_branch` fires once per stage
    agent. `repo_root` is no longer a stand-in for it: since 779 it is
    routinely the ticket's own worktree.

    Refuses (`DirtyPrimaryCheckoutError`) to *switch* a checkout holding
    uncommitted work other than `ignoring` — the ticket's own recorded paths: the
    switch would carry someone's work-in-progress onto this ticket's branch
    (864). Already on the branch, nothing is carried, and an edit there is kept
    as it always was.
    """
    branch = resolve_ticket_branch(ticket)
    validate_branch_name(branch)

    if not (repo_root / ".git").exists():
        raise ValueError(f"Workspace repo is not a git repository: {repo_root}")
    tree = read_tree(repo_root)
    if tree.branch == branch and is_ancestor(repo_root, start_point, tree.head_sha):
        # Nothing landed on the target since this tree last saw it, so the
        # checkout cannot move it and the merge is a no-op ("Already up to
        # date"). One `merge-base --is-ancestor` establishes that in place of
        # `rev-parse --verify` + `checkout` + `status --porcelain` + `merge`.
        #
        # Both halves of the gate are load-bearing. `tree.branch == branch` is
        # what preserves lg-milestone-that-772's no-loss guarantee: a tree left
        # on another branch — or detached, where `read_tree` reports `""` — is
        # owed the fix-up below, and the fix-up is the only thing that puts it
        # back. The ancestry test is the "nothing landed since" condition; drop
        # it and a refresh that was genuinely owed is skipped. `start_point=""`
        # is not "already reachable", and `is_ancestor` answers False for an
        # empty argument, so no extra branch is needed for it.
        #
        # `_refresh_onto`'s "has uncommitted changes" warning is deliberately
        # not emitted here: there was nothing to refresh, so a dirty tree is
        # told nothing rather than told a refresh was skipped.
        return branch

    # Derived once per call and held — never lazily, never twice. Both consumers
    # (the fix-up below and `_classify_holder` in the handler) are unreachable
    # from the fast path above, which is the only reason it sits here rather
    # than before `read_tree`; two derivations that disagree is the defect
    # lg-milestone-that-916 exists for.
    main_tree = main_working_tree(repo_root)
    if tree.branch != branch:
        # An unknown topology (`main_tree is None`) keeps the value that
        # shipped: the refusal names the shared checkout only where the tree
        # handed over is, or might be, the shared checkout (S-6).
        use = (
            PrimaryCheckoutUse.TICKET_BRANCH_CHECKOUT
            if is_main_working_tree(repo_root, main_tree=main_tree)
            else PrimaryCheckoutUse.TICKET_WORKTREE_BRANCH_CHECKOUT
        )
        require_clean_tree(tree, use=use, ignoring=ignoring)

    try:
        _checkout_branch(repo_root, branch, start_point=start_point, seat=seat)
        return branch
    except subprocess.CalledProcessError as first_exc:
        locked_at = _worktree_lock_path(_process_text(first_exc))
        if not locked_at:
            raise ValueError(_checkout_failure_message(branch, first_exc)) from first_exc
        holder = _classify_holder(locked_at, main_tree)

        repaired, repair_note = _try_free_worktree_lock(repo_root, branch, locked_at, holder=holder)
        if not repaired:
            raise ValueError(
                _checkout_failure_message(
                    branch,
                    first_exc,
                    repair_note=repair_note,
                    holder=holder,
                    start_point=start_point,
                )
            ) from first_exc

        try:
            _checkout_branch(repo_root, branch, start_point=start_point, seat=seat)
        except subprocess.CalledProcessError as second_exc:
            raise ValueError(
                _checkout_failure_message(
                    branch,
                    second_exc,
                    repair_note=f"{repair_note} Retry still failed.",
                    holder=holder,
                    start_point=start_point,
                )
            ) from second_exc
        return branch
