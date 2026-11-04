"""A repository with a remote, and a tree whose child has landed, for publish tests."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from loregarden.models.domain import Ticket, WorkItemType, Workspace
from loregarden.services import base_delivery, tree_publish_scheduler, unpublished_trees
from loregarden.services.land_ticket import land_ticket
from loregarden.services.target_branch import resolve_target_branch
from sqlmodel import Session
from tests.worktree_helpers import commit_on, git, make_repo, make_tree_ticket

PR = "https://github.com/example/repo/pull/7"
FULL_CHAIN = {"commit": True, "push": True, "tree_open_pr": True, "tree_auto_merge": True}
NO_PUSH_CHAIN = {"commit": True, "push": False}


def repo_with_remote(tmp_path: Path) -> Path:
    repo = make_repo(tmp_path)
    remote = tmp_path / "remote.git"
    git(tmp_path, "init", "-q", "--bare", str(remote))
    git(repo, "remote", "add", "origin", str(remote))
    git(repo, "push", "-q", "-u", "origin", "main")
    return repo


def make_workspace(session: Session, repo: Path, slug: str) -> Workspace:
    ws = Workspace(slug=slug, name=slug, repo_path=str(repo))
    session.add(ws)
    session.commit()
    session.refresh(ws)
    return ws


def landed_tree(
    session: Session,
    workspace: Workspace,
    repo: Path,
    *,
    root_id: str = "ms",
    chain: dict | None = None,
    filename: str = "c1.txt",
) -> tuple[Ticket, Ticket, str]:
    """`tree_with_child_branch`, with the child landed and nothing published."""
    ms, child, target = tree_with_child_branch(
        session, workspace, repo, root_id=root_id, chain=chain, filename=filename
    )
    assert land_ticket(session, child, workspace).ok
    session.refresh(child)
    return ms, child, target


def squash_onto_remote_main(repo: Path, filename: str = "c1.txt") -> None:
    """A PR squash-merged on GitHub: the branch's content as one base commit."""
    commit_on(repo, "main", filename, "child work\n")
    git(repo, "push", "-q", "origin", "main")


def fake_gh(*, failing: str = ""):
    """`git_automation._gh`: lists no PR and opens one at `PR`.

    ``failing`` names the `gh pr` subcommand to refuse (`create`). Auto-merge goes
    through the merge queue, not `gh`; refuse it by patching `merge_queue.enqueue`.
    """

    def fake(args, cwd):
        if args[:2] == ["pr", "list"]:
            return subprocess.CompletedProcess(args, 0, stdout="\n", stderr="")
        if failing and args[:2] == ["pr", failing]:
            return subprocess.CompletedProcess(
                args, 1, stdout="", stderr=f"gh pr {failing} refused"
            )
        return subprocess.CompletedProcess(args, 0, stdout=f"{PR}\n", stderr="")

    return fake


def tree_with_child_branch(
    session: Session,
    workspace: Workspace,
    repo: Path,
    *,
    root_id: str = "ms",
    chain: dict | None = None,
    filename: str = "c1.txt",
) -> tuple[Ticket, Ticket, str]:
    """A milestone and a child with one commit on its branch, not yet landed.

    Returns (root, child, the integration branch it lands on).
    """
    ms = make_tree_ticket(
        session,
        workspace,
        root_id,
        kind=WorkItemType.MILESTONE,
        git_automation_json=json.dumps(chain or FULL_CHAIN),
    )
    child = make_tree_ticket(session, workspace, f"{root_id}-c1", parent=ms)
    target = resolve_target_branch(session, child, workspace, repo_root=repo)
    git(repo, "branch", child.branch, target)
    commit_on(repo, child.branch, filename, "child work\n")
    return ms, child, target


def clear_process_state() -> None:
    """The module-level caches and guards these tests cross."""
    base_delivery._FETCHED.clear()
    base_delivery._NOT_HELD.clear()
    unpublished_trees._ATTEMPTED.clear()
    tree_publish_scheduler._IN_FLIGHT.clear()
    tree_publish_scheduler._AGAIN.clear()
