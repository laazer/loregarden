"""The ticket's own work still uncommitted in its tree, which landing must not leave behind.

A landing merges the branch's commits, so anything left only in the worktree
does not land, and the ticket still derives done. lg-milestone-that-1237's
implement stage edited `handoff_tree_status.py`, never committed it, and
landed its tests alone; four tests went red on the integration branch until
lg-milestone-that-1314 carried the edit across by hand. On 2026-10-21 eight
landed loregarden tickets still had tracked edits sitting in their trees, each
made before the ticket landed.

`handoff_committed_work` already decides whose uncommitted work a tree holds,
but only a stage that writes a handoff asks it. This applies the same rule at
the one step every ticket passes, with one narrowing: an untracked file counts
only when the ticket's runs recorded it. Untracked files in a ticket tree are
mostly generated output (one blobert tree held fifteen baked textures), and
blocking every landing on them would stop more work than the defect does. A
tracked edit is never output: it is a change to the code the ticket was for.
"""

from __future__ import annotations

from pathlib import Path

from loregarden.models.domain import Ticket, Workspace
from loregarden.services.git_subprocess import run_git
from loregarden.services.handoff_committed_work import uncommitted_ticket_work
from loregarden.services.recorded_paths import ticket_recorded_paths
from loregarden.services.ticket_worktree import resolve_ticket_root
from loregarden.services.workspace_paths import resolve_workspace_root
from sqlmodel import Session


def _untracked(root: Path) -> set[str]:
    listed = run_git(
        ["ls-files", "--others", "--exclude-standard", "-z"],
        cwd=str(root),
        check=False,
        capture_output=True,
        text=True,
    )
    if listed.returncode != 0:
        raise RuntimeError(
            f"git ls-files failed in {root}: {(listed.stderr or listed.stdout).strip()}"
        )
    return {path for path in listed.stdout.split("\0") if path}


def uncommitted_at_landing(
    session: Session, ticket: Ticket, workspace: Workspace
) -> tuple[str, ...]:
    """Paths of this ticket's work that exist only in its worktree; () when none.

    A ticket with no worktree of its own runs in the shared checkout, where
    whose dirt it is cannot be told, so it is not judged here (as the handoff
    check does not block on it). Raises ``RuntimeError`` when git cannot list
    the tree: "could not tell" must not read as "nothing uncommitted".
    """
    root = resolve_ticket_root(session, ticket, workspace)
    if root == resolve_workspace_root(workspace):
        return ()
    work = uncommitted_ticket_work(session, ticket, ticket_root=root, is_ticket_worktree=True)
    if not work.blocks_handoff:
        return ()
    untracked = _untracked(root)
    recorded = ticket_recorded_paths(session, ticket)
    return tuple(path for path in work.paths if path not in untracked or path in recorded)


def describe_uncommitted(paths: tuple[str, ...], root_hint: str) -> str:
    """The sentence a blocked landing, or a finding, carries."""
    listed = ", ".join(paths[:10]) + (f" and {len(paths) - 10} more" if len(paths) > 10 else "")
    return (
        f"Not landed: {len(paths)} file(s) of this ticket's work are uncommitted in {root_hint} "
        f"({listed}). The landing merges commits only, so they would be left behind. Commit "
        "them to the ticket's branch, or discard them with `git checkout -- <path>` if they are "
        "not the ticket's work, then finish the ticket again."
    )
