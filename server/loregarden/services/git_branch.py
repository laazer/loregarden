"""Git branch helpers for ticket-scoped agent runs."""

from __future__ import annotations

import logging
import re
import subprocess
from pathlib import Path

from loregarden.models.domain import Ticket
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
        return False


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
        ["worktree", "prune"],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=False,
    )
    note = f"Self-repaired: removed locking worktree {locked_at} so {branch!r} can check out."
    logger.warning(note)
    return True, note


def _checkout_branch(repo_root: Path, branch: str) -> None:
    run_git(
        ["checkout", "-B", branch],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=True,
    )


def ensure_ticket_branch(repo_root: Path, ticket: Ticket) -> str:
    """Checkout or create the branch a ticket should run on.

    If another (non-primary) worktree holds the branch — common with stale Claude
    scratchpads — remove that worktree once and retry. Gate autofix never sees
    this failure because it happens before any agent starts.
    """
    branch = resolve_ticket_branch(ticket)
    validate_branch_name(branch)

    if not (repo_root / ".git").exists():
        raise ValueError(f"Workspace repo is not a git repository: {repo_root}")

    try:
        _checkout_branch(repo_root, branch)
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
            _checkout_branch(repo_root, branch)
        except subprocess.CalledProcessError as second_exc:
            raise ValueError(
                _checkout_failure_message(
                    branch,
                    second_exc,
                    repair_note=f"{repair_note} Retry still failed.",
                )
            ) from second_exc
        return branch
