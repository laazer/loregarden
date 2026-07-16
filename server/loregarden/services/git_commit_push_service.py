"""Commit and push a ticket's workspace changes to its branch."""

from __future__ import annotations

import subprocess

from loregarden.models.domain import Ticket, Workspace
from loregarden.services.git_branch import resolve_ticket_branch, validate_branch_name
from loregarden.services.workspace_paths import resolve_workspace_root
from sqlmodel import Session


class NothingToCommitError(ValueError):
    """Raised when the workspace has no working-tree changes to commit."""


def commit_ticket_working_tree(session: Session, ticket: Ticket, message: str) -> bool:
    """Stage and commit the workspace's current working tree on whatever branch
    is checked out (the ticket's branch, ensured by the stage run that just
    finished). Returns True if a commit was made, False if there was nothing to
    commit or the workspace isn't a git repo. Does not push.

    Used to capture an automatic static-analysis fix on the ticket branch. Runs
    with --no-verify so committing the just-fixed tree doesn't re-trigger the
    same lint hooks that produced the gate failure in the first place.
    """
    workspace = session.get(Workspace, ticket.workspace_id)
    if not workspace:
        return False

    repo_root = resolve_workspace_root(workspace)
    if not (repo_root / ".git").exists():
        return False

    add = subprocess.run(
        ["git", "add", "-A"],
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
