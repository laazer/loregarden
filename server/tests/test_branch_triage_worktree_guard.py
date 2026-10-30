"""Branch Triage never destroys work when it removes a worktree.

It used to run `git worktree remove --force` with no check, so uncommitted and
untracked work went with the directory, and the client always asked for that.
Every removal now passes the receipt gate (`retire_worktree`), and the gate's
answer carries `checkout_guard.checkout_removal_refusal` as its second premise.

That second premise is what this module is about, and it is the half the
receipt cannot speak for. A receipt says the branch's work landed; it says
nothing about an `.env`, a database or a key git was told to ignore, which
`git status` never lists and which is exactly what somebody keeps in a
worktree and nowhere else. So each test here grants the receipt first — the
tree is cleared to go — and then asserts it is kept anyway, and that the
refusal reaches the caller as an outcome rather than as the absence of an
error.

The receipt gate's own premises, and the chokepoint that routes every call
site through it, are `test_worktree_gate_chokepoint.py`.

Real repositories in `tmp_path`: the guard shells out to git, and a mocked git
would only prove the mock.
"""

from __future__ import annotations

from pathlib import Path
from unittest import mock

import pytest
from loregarden.models.domain import RemovalOutcome, Workspace, Worktree, WorktreeState
from loregarden.services.branch_triage_service import (
    branch_triage_snapshot,
    delete_branch,
    remove_branch_worktree,
)
from loregarden.services.checkout_guard import checkout_removal_refusal
from loregarden.services.directory_release import DirectoryOutsideBase
from loregarden.services.ticket_worktree import resolve_execution_root
from loregarden.services.worktree_service import CleanupRefusal, WorktreeService
from sqlmodel import Session
from tests.factories import make_agent_run, make_workspace
from tests.worktree_helpers import (
    git,
    grant_receipt,
    make_repo,
    make_run,
    make_ticket_on_id_branch,
)


def _write(path: Path, text: str = "x") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def _git(cwd: Path, *args: str) -> str:
    return git(cwd, *args).stdout


def _add_worktree(repo: Path, branch: str) -> Path:
    path = repo.parent / f"wt-{branch}"
    git(repo, "worktree", "add", "-q", "-b", branch, str(path))
    return path.resolve()


@pytest.fixture(name="session")
def session_fixture(isolated_db):
    with Session(isolated_db) as session:
        yield session


@pytest.fixture(name="repo")
def repo_fixture(tmp_path) -> Path:
    repo = make_repo(tmp_path)
    _write(repo / ".gitignore", "node_modules/\n.env\n")
    _write(repo / "app.py")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "ignores")
    return repo.resolve()


@pytest.fixture(name="workspace")
def workspace_fixture(session, repo) -> Workspace:
    return make_workspace(session, slug="guarded", repo_path=str(repo))


def _cleared_tree(session, workspace, repo: Path, external_id: str = "LG-1"):
    """A tracked checkout the receipt gate has already cleared for removal.

    Everything the gate asks about is true: an ACTIVE row with an owner, a
    commit on the branch, and the landing on record. What is left to refuse it
    is `checkout_guard` alone, which is this module's subject.
    """
    ticket = make_ticket_on_id_branch(session, workspace, external_id=external_id)
    run = make_run(session, workspace, ticket, code=f"run-{external_id}")
    path = resolve_execution_root(session, run, ticket, workspace)
    _write(path / "work.txt", "real work\n")
    git(path, "add", "-A")
    git(path, "commit", "-q", "-m", "work")
    grant_receipt(session, ticket, repo)
    return ticket, path


def _worktree_paths(repo: Path) -> set[str]:
    return {
        line.removeprefix("worktree ")
        for line in git(repo, "worktree", "list", "--porcelain").stdout.splitlines()
        if line.startswith("worktree ")
    }


# --- remove one worktree -----------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "content", "matches"),
    [
        ("notes.md", "draft", "uncommitted or untracked"),
        ("app.py", "changed", "uncommitted"),
        (".env", "TOKEN=secret", ".env"),
    ],
    ids=["untracked", "modified-tracked", "ignored-env"],
)
def test_a_worktree_holding_work_of_its_own_is_kept(
    session, workspace, repo, name, content, matches
) -> None:
    ticket, tree = _cleared_tree(session, workspace, repo)
    _write(tree / name, content)

    report = remove_branch_worktree(session, workspace, ticket.branch, str(tree))

    assert report.outcome is RemovalOutcome.REFUSED
    assert matches in report.detail
    # A refusal is an answer, not a raise: the gate worked and nothing was lost.
    assert (tree / name).read_text() == content


def test_a_clean_worktree_goes_and_its_branch_stays(session, workspace, repo) -> None:
    ticket, tree = _cleared_tree(session, workspace, repo)
    # Ignored, and a package manager puts it back: not a reason to keep a tree.
    _write(tree / "node_modules" / "pkg" / "index.js")

    report = remove_branch_worktree(session, workspace, ticket.branch, str(tree))

    assert report.outcome is RemovalOutcome.DELETED
    assert not tree.exists()
    assert str(tree) not in _worktree_paths(repo)
    assert ticket.branch in git(repo, "branch", "--list", ticket.branch).stdout


# --- delete a branch with its worktrees ------------------------------------------------


def test_delete_checks_every_worktree_before_removing_any(session, workspace, repo) -> None:
    ticket, clean = _cleared_tree(session, workspace, repo)
    dirty = repo.parent / "wt-second"
    git(repo, "worktree", "add", "-q", "--force", str(dirty), ticket.branch)
    run = make_run(session, workspace, ticket, code="run-second")
    session.add(
        Worktree(
            workspace_id=workspace.id,
            agent_run_id=run.id,
            ticket_id=ticket.id,
            branch=ticket.branch,
            worktree_path=str(dirty),
            state=WorktreeState.ACTIVE,
        )
    )
    session.commit()
    _write(dirty / "wip.py")

    report = delete_branch(session, workspace, ticket.branch, force=True, remove_worktrees=True)

    assert report.outcome is RemovalOutcome.REFUSED
    assert clean.exists(), "a refusal must come before the first removal"
    assert (dirty / "wip.py").exists()
    assert ticket.branch in git(repo, "branch", "--list", ticket.branch).stdout


def test_the_base_branch_is_refused_before_its_worktrees_are_touched(
    session, workspace, repo
) -> None:
    git(repo, "checkout", "-q", "-b", "dev")
    base_tree = repo.parent / "wt-main"
    git(repo, "worktree", "add", "-q", str(base_tree), "main")

    with pytest.raises(ValueError, match="base branch"):
        delete_branch(session, workspace, "main", force=True, remove_worktrees=True)

    assert base_tree.exists()


def test_delete_with_a_cleared_worktree_removes_it_and_the_branch(session, workspace, repo) -> None:
    ticket, tree = _cleared_tree(session, workspace, repo)

    report = delete_branch(session, workspace, ticket.branch, force=True, remove_worktrees=True)

    assert report.outcome is RemovalOutcome.DELETED
    assert report.deleted is True
    assert not tree.exists()
    assert git(repo, "branch", "--list", ticket.branch).stdout.strip() == ""


# --- the shared guard ----------------------------------------------------------------


def test_the_guard_refuses_a_commit_only_head_reaches(repo) -> None:
    tree = repo.parent / "wt-detached"
    git(repo, "worktree", "add", "-q", "-b", "feature", str(tree))
    git(tree, "checkout", "-q", "--detach")
    _write(tree / "more.py")
    git(tree, "add", ".")
    git(tree, "commit", "-q", "-m", "detached work")

    refusal = checkout_removal_refusal(tree.resolve())

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

        # The refusal is a raise now, not a return: `retire_worktree` is the
        # one place that catches it, so a release reported as done cannot have
        # left the directory behind (lg-external-harness-1026).
        with pytest.raises(DirectoryOutsideBase) as raised:
            service.remove_checkout(row)
        assert raised.value.refusal is CleanupRefusal.OUTSIDE_BASE
        assert raised.value.uncleaned_path == str(sibling)

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


def test_commits_only_this_branch_has_are_refused_and_counted(session, workspace, repo) -> None:
    _commit_on(repo, "feature", "a.py")
    _commit_on(repo, "feature", "b.py")

    with pytest.raises(ValueError, match="2 commits that no other branch, tag or remote has"):
        delete_branch(session, workspace, "feature")

    assert _branch_exists(repo, "feature")


def test_refusing_lost_commits_comes_before_removing_a_worktree(session, workspace, repo) -> None:
    tree = _add_worktree(repo, "feature")
    _write(tree / "work.py")
    _git(tree, "add", "work.py")
    _git(tree, "commit", "-q", "-m", "work")

    with pytest.raises(ValueError, match="1 commit that no other"):
        delete_branch(session, workspace, "feature", remove_worktrees=True)

    assert tree.exists()


def test_discarding_takes_the_explicit_force(session, workspace, repo) -> None:
    _commit_on(repo, "feature", "a.py")

    assert delete_branch(session, workspace, "feature", force=True).deleted is True

    assert not _branch_exists(repo, "feature")


def test_a_pushed_branch_goes_without_force(session, workspace, repo) -> None:
    sha = _commit_on(repo, "feature", "a.py")
    # What `git push` leaves behind: a remote-tracking ref at the same commit.
    _git(repo, "update-ref", "refs/remotes/origin/feature", sha)

    assert delete_branch(session, workspace, "feature").deleted is True


def test_a_branch_whose_commits_another_branch_has_goes_without_force(
    session, workspace, repo
) -> None:
    sha = _commit_on(repo, "feature", "a.py")
    _git(repo, "branch", "keeper", sha)

    assert delete_branch(session, workspace, "feature").deleted is True
    assert _branch_exists(repo, "keeper")


def test_a_squash_merged_branch_goes_without_force(session, workspace, repo) -> None:
    _commit_on(repo, "feature", "a.py")
    _git(repo, "merge", "-q", "--squash", "feature")
    _git(repo, "commit", "-q", "-m", "squash feature")

    assert delete_branch(session, workspace, "feature").deleted is True


def test_the_snapshot_says_how_many_commits_a_delete_would_lose(session, workspace, repo) -> None:
    _commit_on(repo, "unpushed", "a.py")
    _commit_on(repo, "unpushed", "b.py")
    pushed = _commit_on(repo, "pushed", "c.py")
    _git(repo, "update-ref", "refs/remotes/origin/pushed", pushed)
    _commit_on(repo, "squashed", "d.py")
    _git(repo, "merge", "-q", "--squash", "squashed")
    _git(repo, "commit", "-q", "-m", "squash")

    snapshot = branch_triage_snapshot(session, workspace)

    counts = {b["name"]: b["commits_only_here"] for b in snapshot["branches"]}
    assert counts["unpushed"] == 2
    assert counts["pushed"] == 0
    assert counts["squashed"] == 0
    assert counts["main"] == 0


def test_a_count_git_cannot_give_keeps_the_branch(session, workspace, repo) -> None:
    """Unknown is not zero: a failed count refuses, like every other guard here."""
    _commit_on(repo, "feature", "a.py")

    with (
        mock.patch(
            "loregarden.services.branch_triage_service.commits_only_here", return_value=None
        ),
        pytest.raises(ValueError, match="Could not tell"),
    ):
        delete_branch(session, workspace, "feature")

    assert _branch_exists(repo, "feature")
