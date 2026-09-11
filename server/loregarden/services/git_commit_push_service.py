"""Commit and push a ticket's workspace changes to its branch."""

from __future__ import annotations

import logging
import subprocess
from collections.abc import Iterable
from pathlib import Path

from loregarden.models.domain import Ticket, Workspace
from loregarden.services.git_branch import resolve_ticket_branch, validate_branch_name
from loregarden.services.git_subprocess import run_git
from loregarden.services.ticket_worktree import resolve_ticket_root
from loregarden.services.workspace_paths import resolve_workspace_root
from sqlmodel import Session

logger = logging.getLogger(__name__)


class NothingToCommitError(ValueError):
    """Raised when the workspace has no working-tree changes to commit."""


logger = logging.getLogger(__name__)


def _git_reading(
    args: list[str], *, repo_root: Path, describing: str
) -> subprocess.CompletedProcess | None:
    """Run a read-only git command, or None if git could not answer.

    The single place the "None means I could not look" contract of
    `working_tree_paths` and `paths_committed_since` is honoured, because both
    used to honour only HALF of it. They checked `returncode`, which covers a
    git that ran and failed — and missed the case where git never ran at all.

    `run_git` passes `cwd` straight to `subprocess.run`, which raises
    FileNotFoundError when the directory is gone. That escaped both functions as
    an exception rather than the None their callers are written to expect, and
    `record_run_evidence` is called with no try/except immediately before
    `complete_run` — so a removed worktree did not degrade to "changed paths not
    recorded", it abandoned the run mid-completion and left it RUNNING with
    nothing behind it (lg-workflow-integrity-713).

    NotADirectoryError and PermissionError are caught for the same reason: each
    is a way of not being able to look, and the caller cannot tell them apart
    from an empty tree anyway.
    """
    try:
        proc = run_git(args, cwd=repo_root, capture_output=True, text=True)
    except OSError as exc:
        logger.warning("could not run git %s in %s: %s", describing, repo_root, exc)
        return None
    if proc.returncode != 0:
        logger.warning(
            "git %s failed in %s (exit %s): %s",
            describing,
            repo_root,
            proc.returncode,
            (proc.stderr or "").strip()[:400],
        )
        return None
    return proc


def paths_committed_since(repo_root: Path, base_sha: str) -> set[str] | None:
    """Paths the commits after `base_sha` touched, or None if git could not say.

    The companion to `working_tree_paths`, and the reason it is needed: a run
    whose agent commits its own work leaves a CLEAN tree, so the dirty-path delta
    is empty and the run records nothing — indistinguishable from an agent that
    wrote no code. Reproduced against real git; see
    lg-workflow-integrity-406.

    None rather than an empty set when git cannot answer, for the reason the
    whole of 406's first half exists: "I could not look" and "there was nothing
    there" are different facts, and collapsing them is what made the original
    measurement unrecoverable.

    An empty `base_sha` is not a failure — a repository with no commit at its
    start has nothing to diff against, and its work will be uncommitted anyway,
    so the dirty set already covers it.
    """
    if not base_sha:
        return set()
    proc = _git_reading(
        ["diff", "--name-only", "-z", f"{base_sha}..HEAD"],
        repo_root=repo_root,
        describing=f"diff {base_sha}..HEAD",
    )
    if proc is None:
        return None
    return {path for path in proc.stdout.split("\0") if path}


def working_tree_paths(repo_root: Path) -> set[str] | None:
    """Every path git currently reports as dirty, untracked included.

    Returns None when git could not answer — NOT an empty set. The two are
    different facts and this function used to report both as "nothing is dirty",
    which is the shape lg-workflow-integrity-450 fixed for gates: a diagnostic
    that cannot tell "the check passed" from "the check could not run" reports
    success it never had.

    It matters most for `_record_changed_paths`, which stores this as the record
    of what a run touched. A failed `git status` there produced an empty delta,
    which was written as "this run changed nothing" — indistinguishable in the
    database from a run that genuinely changed nothing, and unrecoverable
    afterwards. Two thirds of loregarden's own code-writing runs record nothing
    (lg-workflow-integrity-406), and until this told the difference there was no
    way to know how much of that was real.

    `-z` because paths with spaces or non-ASCII are otherwise quoted and would
    not round-trip back into `git add`.
    """
    proc = _git_reading(
        ["status", "--porcelain=v1", "-z", "--untracked-files=all"],
        repo_root=repo_root,
        describing="status",
    )
    if proc is None:
        return None

    records = [record for record in proc.stdout.split("\0") if record]
    paths: set[str] = set()
    index = 0
    while index < len(records):
        entry = records[index]
        status, path = entry[:2], entry[3:]
        if path:
            paths.add(path)
        # A rename or copy emits its source as the following record.
        if status[:1] in ("R", "C"):
            index += 1
            if index < len(records):
                paths.add(records[index])
        index += 1
    return paths


def head_commit_sha(repo_root: Path) -> str:
    """Current HEAD, or "" when the workspace has no commits or is not a repo.

    Evidence is stamped with this server-side rather than taken from the agent:
    an agent that picks its own sha can claim proof against a commit its work
    predates.

    "Not a repo" includes "not a directory". A workspace whose repo_path does not
    exist on this machine made `subprocess` raise on the missing cwd rather than
    returning the empty string this promises — so a caller doing nothing more
    than asking which commit is current inherited an exception from a path it
    never touched.
    """
    try:
        proc = run_git(
            ["rev-parse", "HEAD"],
            cwd=repo_root,
            capture_output=True,
            text=True,
        )
    except OSError:
        logger.warning(
            "Could not read HEAD of %s; evidence for this run carries no commit sha",
            repo_root,
            exc_info=True,
        )
        return ""
    return proc.stdout.strip() if proc.returncode == 0 else ""


def commit_paths(session: Session, ticket: Ticket, message: str, paths: Iterable[str]) -> bool:
    """Commit only `paths` on the checked-out branch. Returns False if nothing staged.

    The scoped counterpart to committing the whole tree: anything the ticket's
    own work did not touch stays uncommitted, so unrelated edits sitting in the
    workspace are never swept into this ticket's history.
    """
    wanted = sorted({path for path in paths if path})
    if not wanted:
        return False

    workspace = session.get(Workspace, ticket.workspace_id)
    if not workspace:
        return False
    if not (resolve_workspace_root(workspace) / ".git").exists():
        return False
    # The stage wrote these paths in the ticket's worktree. Staging them in the
    # shared checkout would match nothing and silently commit no work.
    repo_root = resolve_ticket_root(session, ticket, workspace)
    return commit_paths_in(repo_root, message, wanted)


def commit_paths_in(repo_root: Path, message: str, paths: Iterable[str]) -> bool:
    """`commit_paths` against an explicit tree, for callers that know theirs.

    A fan-out attempt's tree is one of those: it belongs to the attempt, not to
    the ticket, so resolving the ticket's worktree would commit in the wrong
    place.
    """
    wanted = sorted({path for path in paths if path})
    if not wanted:
        return False

    # Only stage what is still dirty; a path recorded earlier may have been
    # committed or reverted since, and `git add` on a pathspec matching nothing
    # is an error rather than a no-op.
    # Nothing to commit if we cannot see what is dirty — better than
    # committing a guess.
    live = (working_tree_paths(repo_root) or set()) & set(wanted)
    if not live:
        return False

    add = run_git(
        ["add", "--", *sorted(live)],
        cwd=repo_root,
        capture_output=True,
        text=True,
    )
    if add.returncode != 0:
        raise ValueError((add.stderr or add.stdout or "git add failed").strip())

    commit = run_git(
        ["commit", "-m", message],
        cwd=repo_root,
        capture_output=True,
        text=True,
    )
    if commit.returncode != 0:
        combined = f"{commit.stdout}\n{commit.stderr}".lower()
        if "nothing to commit" in combined:
            return False
        raise ValueError((commit.stderr or commit.stdout or "git commit failed").strip())
    return True


def commit_and_push_ticket_branch(session: Session, ticket: Ticket) -> dict:
    """Commit and push the whole working tree for an operator-triggered request.

    Still `git add -A`, deliberately: this runs when a human asks to commit a
    ticket's branch, and their hand edits are part of that intent but were never
    recorded against any agent run. Scoping it to recorded paths would silently
    commit nothing. Automated commits go through commit_paths instead.
    """
    workspace = session.get(Workspace, ticket.workspace_id)
    if not workspace:
        raise ValueError("Workspace not found")

    if not (resolve_workspace_root(workspace) / ".git").exists():
        raise ValueError("Workspace repo is not a git repository")

    # The ticket's tree, which is its worktree once it has run: `git add -A` in
    # the shared checkout would commit whatever else is sitting there and miss
    # everything the stages wrote.
    repo_root = resolve_ticket_root(session, ticket, workspace)

    branch = resolve_ticket_branch(ticket)
    validate_branch_name(branch)

    add = run_git(
        ["add", "-A"],
        cwd=repo_root,
        capture_output=True,
        text=True,
    )
    if add.returncode != 0:
        raise ValueError((add.stderr or add.stdout or "git add failed").strip())

    message = f"{ticket.external_id}: {ticket.title}"
    commit = run_git(
        ["commit", "-m", message],
        cwd=repo_root,
        capture_output=True,
        text=True,
    )
    if commit.returncode != 0:
        combined = f"{commit.stdout}\n{commit.stderr}".lower()
        if "nothing to commit" in combined:
            raise NothingToCommitError(
                "No changes to commit on this branch. Check Branch Triage to review its state."
            )
        raise ValueError((commit.stderr or commit.stdout or "git commit failed").strip())

    push = run_git(
        ["push", "-u", "origin", branch],
        cwd=repo_root,
        capture_output=True,
        text=True,
    )
    if push.returncode != 0:
        raise ValueError((push.stderr or push.stdout or "git push failed").strip())

    return {"branch": branch, "committed": True, "pushed": True}
