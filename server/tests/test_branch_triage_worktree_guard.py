"""Branch Triage never destroys work when it removes a worktree.

It used to run `git worktree remove --force` with no check, so uncommitted and
untracked work went with the directory, and the client always asked for that.
Every removal now passes `checkout_guard.checkout_removal_refusal` first, a
delete checks every worktree before removing any, and the base branch is
refused before anything is touched.

Real repositories in `tmp_path`: the guard shells out to git, and a mocked git
would only prove the mock.
"""

from __future__ import annotations

import os
from pathlib import Path
from unittest import mock

import pytest
from loregarden.models.domain import Workspace, Worktree, WorktreeState
from loregarden.services.branch_triage_service import (
    branch_triage_snapshot,
    delete_branch,
    remove_branch_worktree,
)
from loregarden.services.checkout_guard import checkout_removal_refusal
from loregarden.services.git_subprocess import run_git
from loregarden.services.worktree_service import WorktreeService
from sqlmodel import Session
from tests.factories import make_agent_run, make_workspace


def _git(cwd: Path, *args: str) -> str:
    return run_git(list(args), cwd=str(cwd), check=True, capture_output=True, text=True).stdout


def _write(path: Path, text: str = "x") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


@pytest.fixture(name="repo")
def repo_fixture(tmp_path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "t")
    _write(repo / ".gitignore", "node_modules/\n.env\n")
    _write(repo / "app.py")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "init")
    return repo.resolve()


@pytest.fixture(name="workspace")
def workspace_fixture(isolated_db, repo) -> Workspace:
    with Session(isolated_db) as session:
        return make_workspace(session, slug="guarded", repo_path=str(repo))


def _add_worktree(repo: Path, branch: str) -> Path:
    path = repo.parent / f"wt-{branch}"
    _git(repo, "worktree", "add", "-q", "-b", branch, str(path))
    return path.resolve()


def _worktree_paths(repo: Path) -> set[str]:
    return {
        line.removeprefix("worktree ")
        for line in _git(repo, "worktree", "list", "--porcelain").splitlines()
        if line.startswith("worktree ")
    }


# --- remove one worktree -----------------------------------------------------------


def test_a_worktree_with_untracked_work_is_kept(workspace, repo) -> None:
    tree = _add_worktree(repo, "feature")
    _write(tree / "notes.md", "draft")

    with pytest.raises(ValueError, match="uncommitted or untracked"):
        remove_branch_worktree(workspace, "feature", str(tree))

    assert (tree / "notes.md").read_text() == "draft"


def test_a_worktree_with_a_modified_tracked_file_is_kept(workspace, repo) -> None:
    tree = _add_worktree(repo, "feature")
    _write(tree / "app.py", "changed")

    with pytest.raises(ValueError, match="uncommitted"):
        remove_branch_worktree(workspace, "feature", str(tree))

    assert (tree / "app.py").read_text() == "changed"


def test_a_worktree_holding_an_ignored_env_file_is_kept(workspace, repo) -> None:
    tree = _add_worktree(repo, "feature")
    _write(tree / ".env", "TOKEN=secret")

    with pytest.raises(ValueError, match=r"\.env"):
        remove_branch_worktree(workspace, "feature", str(tree))

    assert (tree / ".env").exists()


def test_a_clean_worktree_goes_and_its_branch_stays(workspace, repo) -> None:
    tree = _add_worktree(repo, "feature")
    _write(tree / "node_modules" / "pkg" / "index.js")

    remove_branch_worktree(workspace, "feature", str(tree))

    assert not tree.exists()
    assert str(tree) not in _worktree_paths(repo)
    assert "feature" in _git(repo, "branch", "--list", "feature")


def test_a_worktree_whose_directory_is_already_gone_is_pruned(workspace, repo) -> None:
    tree = _add_worktree(repo, "feature")
    # Simulate a directory deleted by hand; git still lists the worktree.
    for root, dirs, files in os.walk(tree, topdown=False):
        for name in files:
            Path(root, name).unlink()
        for name in dirs:
            Path(root, name).rmdir()
    tree.rmdir()

    remove_branch_worktree(workspace, "feature", str(tree))

    assert str(tree) not in _worktree_paths(repo)


# --- delete a branch with its worktrees ------------------------------------------------


def test_delete_checks_every_worktree_before_removing_any(workspace, repo) -> None:
    clean = _add_worktree(repo, "feature")
    dirty = repo.parent / "wt-feature-2"
    _git(repo, "worktree", "add", "-q", "--force", str(dirty), "feature")
    _write(dirty / "wip.py")

    with pytest.raises(ValueError, match="uncommitted or untracked"):
        delete_branch(workspace, "feature", force=True, remove_worktrees=True)

    assert clean.exists(), "a refusal must come before the first removal"
    assert (dirty / "wip.py").exists()
    assert "feature" in _git(repo, "branch", "--list", "feature")


def test_the_base_branch_is_refused_before_its_worktrees_are_touched(workspace, repo) -> None:
    _git(repo, "checkout", "-q", "-b", "dev")
    base_tree = repo.parent / "wt-main"
    _git(repo, "worktree", "add", "-q", str(base_tree), "main")

    with pytest.raises(ValueError, match="base branch"):
        delete_branch(workspace, "main", force=True, remove_worktrees=True)

    assert base_tree.exists()


def test_delete_with_clean_worktrees_removes_them_and_the_branch(workspace, repo) -> None:
    tree = _add_worktree(repo, "feature")

    assert delete_branch(workspace, "feature", force=True, remove_worktrees=True) is True

    assert not tree.exists()
    assert _git(repo, "branch", "--list", "feature").strip() == ""


# --- the shared guard ----------------------------------------------------------------


def test_the_guard_refuses_a_commit_only_head_reaches(repo) -> None:
    tree = _add_worktree(repo, "feature")
    _git(tree, "checkout", "-q", "--detach")
    _write(tree / "more.py")
    _git(tree, "add", ".")
    _git(tree, "commit", "-q", "-m", "detached work")

    refusal = checkout_removal_refusal(tree)

    assert refusal is not None and "branch or tag" in refusal


def test_the_guard_refuses_the_main_checkout(repo) -> None:
    refusal = checkout_removal_refusal(repo)
    assert refusal is not None and "main checkout" in refusal


# --- ticket worktree containment ----------------------------------------------------------


def test_cleanup_refuses_a_sibling_whose_name_only_starts_like_the_base(isolated_db, repo) -> None:
    sibling = repo.parent / ".worktrees-x" / "victim"
    _write(sibling / "keep.txt")
    with Session(isolated_db) as session:
        ws = make_workspace(session, slug="contained", repo_path=str(repo))
        run = make_agent_run(session, workspace_id=ws.id)
        row = Worktree(
            workspace_id=ws.id,
            agent_run_id=run.id,
            worktree_path=str(sibling),
            state=WorktreeState.ACTIVE,
        )
        session.add(row)
        session.commit()
        service = WorktreeService(session, repo_path=str(repo))

        assert service.cleanup_worktree(row) is False

    assert (sibling / "keep.txt").exists()


# --- deleting a branch never drops commits that exist nowhere else ------------------------


def _commit_on(repo: Path, branch: str, name: str) -> str:
    exists = _git(repo, "branch", "--list", branch).strip() != ""
    _git(repo, "checkout", "-q", *([branch] if exists else ["-b", branch]))
    _write(repo / name, name)
    _git(repo, "add", name)
    _git(repo, "commit", "-q", "-m", name)
    sha = _git(repo, "rev-parse", "HEAD").strip()
    _git(repo, "checkout", "-q", "main")
    return sha


def _branch_exists(repo: Path, branch: str) -> bool:
    return _git(repo, "branch", "--list", branch).strip() != ""


def test_commits_only_this_branch_has_are_refused_and_counted(workspace, repo) -> None:
    _commit_on(repo, "feature", "a.py")
    _commit_on(repo, "feature", "b.py")

    with pytest.raises(ValueError, match="2 commits that no other branch, tag or remote has"):
        delete_branch(workspace, "feature")

    assert _branch_exists(repo, "feature")


def test_refusing_lost_commits_comes_before_removing_a_worktree(workspace, repo) -> None:
    tree = _add_worktree(repo, "feature")
    _write(tree / "work.py")
    _git(tree, "add", "work.py")
    _git(tree, "commit", "-q", "-m", "work")

    with pytest.raises(ValueError, match="1 commit that no other"):
        delete_branch(workspace, "feature", remove_worktrees=True)

    assert tree.exists()


def test_discarding_takes_the_explicit_force(workspace, repo) -> None:
    _commit_on(repo, "feature", "a.py")

    assert delete_branch(workspace, "feature", force=True) is True

    assert not _branch_exists(repo, "feature")


def test_a_pushed_branch_goes_without_force(workspace, repo) -> None:
    sha = _commit_on(repo, "feature", "a.py")
    # What `git push` leaves behind: a remote-tracking ref at the same commit.
    _git(repo, "update-ref", "refs/remotes/origin/feature", sha)

    assert delete_branch(workspace, "feature") is True


def test_a_branch_whose_commits_another_branch_has_goes_without_force(workspace, repo) -> None:
    sha = _commit_on(repo, "feature", "a.py")
    _git(repo, "branch", "keeper", sha)

    assert delete_branch(workspace, "feature") is True
    assert _branch_exists(repo, "keeper")


def test_a_squash_merged_branch_goes_without_force(workspace, repo) -> None:
    _commit_on(repo, "feature", "a.py")
    _git(repo, "merge", "-q", "--squash", "feature")
    _git(repo, "commit", "-q", "-m", "squash feature")

    assert delete_branch(workspace, "feature") is True


def test_the_snapshot_says_how_many_commits_a_delete_would_lose(
    isolated_db, workspace, repo
) -> None:
    _commit_on(repo, "unpushed", "a.py")
    _commit_on(repo, "unpushed", "b.py")
    pushed = _commit_on(repo, "pushed", "c.py")
    _git(repo, "update-ref", "refs/remotes/origin/pushed", pushed)
    _commit_on(repo, "squashed", "d.py")
    _git(repo, "merge", "-q", "--squash", "squashed")
    _git(repo, "commit", "-q", "-m", "squash")

    with Session(isolated_db) as session:
        snapshot = branch_triage_snapshot(session, workspace)

    counts = {b["name"]: b["commits_only_here"] for b in snapshot["branches"]}
    assert counts["unpushed"] == 2
    assert counts["pushed"] == 0
    assert counts["squashed"] == 0
    assert counts["main"] == 0


def test_a_count_git_cannot_give_keeps_the_branch(workspace, repo) -> None:
    """Unknown is not zero: a failed count refuses, like every other guard here."""
    _commit_on(repo, "feature", "a.py")

    with (
        mock.patch(
            "loregarden.services.branch_triage_service.commits_only_here", return_value=None
        ),
        pytest.raises(ValueError, match="Could not tell"),
    ):
        delete_branch(workspace, "feature")

    assert _branch_exists(repo, "feature")
