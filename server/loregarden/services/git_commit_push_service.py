"""Commit and push a ticket's workspace changes to its branch."""

from __future__ import annotations

import subprocess
from collections.abc import Iterable
from pathlib import Path

from loregarden.models.domain import Ticket, Workspace
from loregarden.services.git_branch import resolve_ticket_branch, validate_branch_name
from loregarden.services.workspace_paths import resolve_workspace_root
from sqlmodel import Session


class NothingToCommitError(ValueError):
    """Raised when the workspace has no working-tree changes to commit."""


def working_tree_paths(repo_root: Path) -> set[str]:
    """Every path git currently reports as dirty, untracked included.

    Used to bracket a stage run so its commit can be scoped to what it actually
    touched. `-z` because paths with spaces or non-ASCII are otherwise quoted and
    would not round-trip back into `git add`.
    """
    proc = subprocess.run(
        ["git", "status", "--porcelain=v1", "-z", "--untracked-files=all"],
        cwd=repo_root,
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        return set()

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
    repo_root = resolve_workspace_root(workspace)
    if not (repo_root / ".git").exists():
        return False

    # Only stage what is still dirty; a path recorded earlier may have been
    # committed or reverted since, and `git add` on a pathspec matching nothing
    # is an error rather than a no-op.
    live = working_tree_paths(repo_root) & set(wanted)
    if not live:
        return False

    add = subprocess.run(
        ["git", "add", "--", *sorted(live)],
        cwd=repo_root,
        capture_output=True,
        text=True,
    )
    if add.returncode != 0:
        raise ValueError((add.stderr or add.stdout or "git add failed").strip())

    commit = subprocess.run(
        ["git", "commit", "--no-verify", "-m", message],
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

    repo_root = resolve_workspace_root(workspace)
    if not (repo_root / ".git").exists():
        raise ValueError("Workspace repo is not a git repository")

    branch = resolve_ticket_branch(ticket)
    validate_branch_name(branch)

    add = subprocess.run(
        ["git", "add", "-A"],
        cwd=repo_root,
        capture_output=True,
        text=True,
    )
    if add.returncode != 0:
        raise ValueError((add.stderr or add.stdout or "git add failed").strip())

    message = f"{ticket.external_id}: {ticket.title}"
    commit = subprocess.run(
        ["git", "commit", "-m", message],
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

    push = subprocess.run(
        ["git", "push", "-u", "origin", branch],
        cwd=repo_root,
        capture_output=True,
        text=True,
    )
    if push.returncode != 0:
        raise ValueError((push.stderr or push.stdout or "git push failed").strip())

    return {"branch": branch, "committed": True, "pushed": True}
