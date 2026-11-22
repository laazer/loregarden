"""A branch stacked on one that already landed as a squash merges without a rebase.

The stacked branch carries its parent's commits. Once the parent squash-lands,
main shares none of them, and a plain merge of the stacked tip conflicts
wherever it re-edited a line the parent added. Two PRs, one on the other,
took hours to land that way: rebase, push, wait for CI again.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest
from loregarden.services.git_merge_noco import landed_prefix, restack_onto, squash_onto
from tests.worktree_helpers import commit_on, git, make_repo


def _head(repo: Path, ref: str) -> str:
    return git(repo, "rev-parse", ref).stdout.strip()


def _show(repo: Path, sha: str, path: str) -> str:
    return git(repo, "show", f"{sha}:{path}").stdout


def _squash_land(repo: Path, branch: str) -> str:
    """What GitHub's squash merge does: the branch's net change as one commit on main."""
    main = _head(repo, "main")
    tree = git(repo, "merge-tree", "--write-tree", main, branch).stdout.split()[0]
    landed = git(repo, "commit-tree", tree, "-p", main, "-m", f"squash {branch}").stdout.strip()
    git(repo, "update-ref", "refs/heads/main", landed, main)
    git(repo, "reset", "-q", "--hard", "main")
    return landed


@dataclass
class Stack:
    repo: Path
    parent_tip: str
    child_tip: str


@pytest.fixture(name="stack")
def stack_fixture(tmp_path: Path) -> Stack:
    repo = make_repo(tmp_path)
    git(repo, "branch", "parent", "main")
    commit_on(repo, "parent", "a.txt", "a\n")
    parent_tip = commit_on(repo, "parent", "b.txt", "b\n")
    git(repo, "branch", "child", "parent")
    child_tip = commit_on(repo, "child", "b.txt", "b reworked\n")
    return Stack(repo, parent_tip, child_tip)


def test_nothing_has_landed_until_the_parent_does(stack: Stack):
    assert landed_prefix(stack.repo, _head(stack.repo, "main"), stack.child_tip) == ""


def test_once_the_parent_lands_its_tip_is_where_the_replay_starts(stack: Stack):
    _squash_land(stack.repo, "parent")
    main = commit_on(stack.repo, "main", "other.txt", "other\n")

    assert landed_prefix(stack.repo, main, stack.child_tip) == stack.parent_tip


def test_the_stacked_tip_lands_with_main_and_the_parent_intact(stack: Stack):
    _squash_land(stack.repo, "parent")
    main = commit_on(stack.repo, "main", "other.txt", "other\n")
    plain = squash_onto(stack.repo, main, stack.child_tip, message="child")
    assert plain.conflicted_files == ("b.txt",)

    outcome, replay_from = restack_onto(stack.repo, main, stack.child_tip, message="child")

    assert outcome.ok, outcome.detail
    assert replay_from == stack.parent_tip
    assert _head(stack.repo, f"{outcome.sha}^") == main
    assert _show(stack.repo, outcome.sha, "b.txt") == "b reworked\n"
    assert _show(stack.repo, outcome.sha, "a.txt") == "a\n"
    assert _show(stack.repo, outcome.sha, "other.txt") == "other\n"


def test_a_real_conflict_after_the_replay_is_still_reported(stack: Stack):
    _squash_land(stack.repo, "parent")
    main = commit_on(stack.repo, "main", "b.txt", "b changed on main\n")

    outcome, replay_from = restack_onto(stack.repo, main, stack.child_tip, message="child")

    assert not outcome.ok
    assert outcome.conflicted_files == ("b.txt",)
    assert replay_from == ""
