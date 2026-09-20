"""Git branch helpers for ticket-scoped agent runs."""

from __future__ import annotations

import logging
import re
import subprocess
from pathlib import Path

from loregarden.models.domain import BaxterChatSession, Ticket
from loregarden.services.git_subprocess import run_git

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


def _checkout_failure_message(
    branch: str,
    exc: subprocess.CalledProcessError,
    *,
    repair_note: str = "",
) -> str:
    detail = _process_text(exc)
    locked_at = _worktree_lock_path(detail)
    if locked_at:
        prefix = repair_note.strip() + "\n\n" if repair_note.strip() else ""
        return (
            f"{prefix}"
            f"Branch {branch!r} is already checked out in another worktree at "
            f"{locked_at}. Git refuses a second checkout (exit 128).\n\n"
            f"Free it with:\n"
            f"  git worktree remove --force {locked_at}\n"
            f"then retry the run. Stale Claude/Cursor scratchpad worktrees are a "
            f"common cause."
        )
    return detail or f"git checkout -B {branch!r} failed ({exc})"


def _is_primary_checkout(repo_root: Path, worktree_path: str) -> bool:
    try:
        return Path(worktree_path).resolve() == repo_root.resolve()
    except OSError:
        # Fail closed: an unresolvable path may well BE the primary checkout, and
        # answering False here is what authorizes `worktree remove --force`.
        logger.warning(
            "Could not resolve worktree path %r against %s; treating it as the primary checkout",
            worktree_path,
            repo_root,
            exc_info=True,
        )
        return True


def _try_free_worktree_lock(repo_root: Path, branch: str, locked_at: str) -> tuple[bool, str]:
    """Try to remove a non-primary worktree holding `branch`.

    Returns ``(repaired, note)``. ``repaired`` is True only when the lock was
    cleared and checkout should be retried.
    """
    if _is_primary_checkout(repo_root, locked_at):
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


def _checkout_branch(repo_root: Path, branch: str, *, start_point: str) -> None:
    """Check ``branch`` out, creating it from ``start_point`` only if it is missing.

    This was ``checkout -B``, which *resets* an existing branch to HEAD. On any
    re-dispatch — a retry, a rework round after another ticket had moved HEAD, a
    requeue — the ticket's earlier commits were left unreachable and the tree
    looked like a first attempt (lg-milestone-that-772).

    An existing branch is then brought up to ``start_point`` when one is given
    and the tree is clean, so a prerequisite that landed since the branch was
    cut reaches the ticket (lg-milestone-that-769). A dirty tree is left alone
    — merging over uncommitted work is how it gets lost — and said so.
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
        _refresh_onto(repo_root, branch, start_point)


def _refresh_onto(repo_root: Path, branch: str, start_point: str) -> None:
    dirty = run_git(
        ["status", "--porcelain"], cwd=repo_root, capture_output=True, text=True, check=True
    )
    if dirty.stdout.strip():
        logger.warning(
            "Not refreshing %s from %s: %s has uncommitted changes", branch, start_point, repo_root
        )
        return
    merged = run_git(
        ["merge", "--no-edit", start_point],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=False,
    )
    if merged.returncode == 0:
        return
    detail = (merged.stdout or merged.stderr or "git merge failed").strip()
    # silent-ok: cleanup on an already-failing path; the ValueError below carries
    # the merge's own output, and a failed abort leaves conflict markers the
    # next `status --porcelain` refuses to merge over.
    run_git(["merge", "--abort"], cwd=repo_root, capture_output=True, text=True, check=False)
    raise ValueError(f"Branch {branch!r} could not take {start_point!r} before starting: {detail}")


def ensure_ticket_branch(repo_root: Path, ticket: Ticket, *, start_point: str = "") -> str:
    """Checkout or create the branch a ticket should run on.

    ``start_point`` is where a *missing* branch is cut from; empty means HEAD,
    which is where the callers still cut from until the target branch reaches
    them (lg-milestone-that-769). An existing branch is never moved.

    If another (non-primary) worktree holds the branch — common with stale Claude
    scratchpads — remove that worktree once and retry. Gate autofix never sees
    this failure because it happens before any agent starts.
    """
    branch = resolve_ticket_branch(ticket)
    validate_branch_name(branch)

    if not (repo_root / ".git").exists():
        raise ValueError(f"Workspace repo is not a git repository: {repo_root}")

    try:
        _checkout_branch(repo_root, branch, start_point=start_point)
        return branch
    except subprocess.CalledProcessError as first_exc:
        locked_at = _worktree_lock_path(_process_text(first_exc))
        if not locked_at:
            raise ValueError(_checkout_failure_message(branch, first_exc)) from first_exc

        repaired, repair_note = _try_free_worktree_lock(repo_root, branch, locked_at)
        if not repaired:
            raise ValueError(
                _checkout_failure_message(branch, first_exc, repair_note=repair_note)
            ) from first_exc

        try:
            _checkout_branch(repo_root, branch, start_point=start_point)
        except subprocess.CalledProcessError as second_exc:
            raise ValueError(
                _checkout_failure_message(
                    branch,
                    second_exc,
                    repair_note=f"{repair_note} Retry still failed.",
                )
            ) from second_exc
        return branch
