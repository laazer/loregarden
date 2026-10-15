"""Which checkout a path belongs to, answered from any linked worktree.

`services/git_trees` exists because two decisions inside `ensure_ticket_branch`
were reading `repo_root` as the repository's main working tree, and once 779
stopped skipping that call for worktree runs `repo_root` is routinely the
ticket's own worktree (lg-milestone-that-916). The answer has to come from git,
once, and it has to fail closed: answering "this is not the main tree" is what
authorizes `git worktree remove --force`.

So every test here is about one of two things — the answer git gives, and what
is answered when git cannot.
"""

from __future__ import annotations

import logging
import subprocess
import sys
from pathlib import Path
from unittest import mock

import pytest
from loregarden.services.git_subprocess import scrubbed_git_env
from loregarden.services.git_trees import is_main_working_tree, main_working_tree


def _git(args: list[str], *, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
        env=scrubbed_git_env(),
    )


@pytest.fixture(name="main_tree")
def main_tree_fixture(tmp_path: Path) -> Path:
    """A repository's main working tree, with one commit on `main`."""
    root = tmp_path / "primary"
    root.mkdir()
    _git(["init", "-q", "-b", "main"], cwd=root)
    _git(["config", "user.email", "test@example.com"], cwd=root)
    _git(["config", "user.name", "Test"], cwd=root)
    (root / "README.md").write_text("# test\n", encoding="utf-8")
    _git(["add", "."], cwd=root)
    _git(["commit", "-q", "-m", "init"], cwd=root)
    return root


@pytest.fixture(name="linked")
def linked_fixture(tmp_path: Path, main_tree: Path) -> Path:
    """A linked worktree of `main_tree`, on a branch of its own."""
    tree = tmp_path / "linked"
    _git(["worktree", "add", "-q", "-b", "elsewhere", str(tree)], cwd=main_tree)
    return tree


# --- S-1: one answer, from git ---------------------------------------------


def test_the_main_working_tree_is_answered_from_a_linked_worktree(main_tree, linked):
    """The whole point: asked inside the worktree, git names the main tree."""
    assert main_working_tree(linked) == main_tree.resolve()


def test_the_main_working_tree_asked_in_the_main_tree_is_itself(main_tree):
    assert main_working_tree(main_tree) == main_tree.resolve()


def test_a_directory_that_is_not_a_repository_has_no_answer(tmp_path):
    """`git_reading`'s "None means I could not look" contract, borrowed."""
    outside = tmp_path / "not-a-repo"
    outside.mkdir()

    assert main_working_tree(outside) is None


def test_a_directory_that_is_gone_has_no_answer(tmp_path):
    """git never runs at all — the half of the contract that used to escape."""
    assert main_working_tree(tmp_path / "vanished") is None


def test_importing_git_trees_pulls_in_no_database_module():
    """S-1's layering, as a fact rather than an intention.

    The duplicate this module deduplicates lives in `db/shared_database_guard`,
    which will import *up* into here. A `loregarden.db` import from here is the
    cycle that makes that impossible.
    """
    probe = (
        "import loregarden.services.git_trees, sys;"
        "print([m for m in sys.modules if m.startswith('loregarden.db')])"
    )
    result = subprocess.run(
        [sys.executable, "-c", probe], check=True, capture_output=True, text=True
    )

    assert result.stdout.strip() == "[]", result.stdout


# --- S-2: fail closed, in both directions ----------------------------------


def test_a_linked_worktree_is_not_the_main_tree(main_tree, linked):
    """The one answer that authorizes a force-removal, so it must still be given."""
    assert is_main_working_tree(linked, main_tree=main_tree) is False


def test_the_main_tree_is_recognised(main_tree):
    assert is_main_working_tree(main_tree, main_tree=main_tree) is True


def test_a_topology_git_could_not_read_is_treated_as_the_main_tree(main_tree, linked):
    """`main_tree is None` means "I could not look", never "it is safe"."""
    assert is_main_working_tree(linked, main_tree=None) is True


def test_a_path_that_cannot_be_resolved_is_treated_as_the_main_tree(main_tree, caplog):
    """An unresolvable path may well BE the main tree, and the removal is forced.

    Today's `_is_primary_checkout` OSError branch, kept verbatim — and kept
    audible, because a guard that fires silently is indistinguishable from one
    that never fired.
    """
    with (
        caplog.at_level(logging.WARNING),
        mock.patch.object(Path, "resolve", side_effect=OSError("boom")),
    ):
        answer = is_main_working_tree("/some/unresolvable/path", main_tree=main_tree)

    assert answer is True
    assert caplog.records, "a guard that fails closed must say that it did"
    assert any(record.levelno >= logging.WARNING for record in caplog.records)


def test_the_main_tree_is_recognised_through_a_symlinked_parent(tmp_path, main_tree):
    """T5. The reproduction in the ticket runs under `/tmp`, which on macOS is a
    symlink to `/private/tmp`. Comparing one resolved side against one
    unresolved side answers False there — and False is what forces the removal.
    """
    link = tmp_path / "link"
    link.symlink_to(main_tree.parent, target_is_directory=True)
    through_the_link = link / main_tree.name
    assert through_the_link != main_tree, "fixture: the two spellings must differ"

    assert is_main_working_tree(through_the_link, main_tree=main_tree) is True


def test_a_main_tree_given_unresolved_still_matches(tmp_path, main_tree):
    """The symlink case from the other side: it is the *stored* value that is
    unresolved. Both sides are resolved before comparing, or neither works.
    """
    link = tmp_path / "link"
    link.symlink_to(main_tree.parent, target_is_directory=True)

    assert is_main_working_tree(main_tree, main_tree=link / main_tree.name) is True


def test_a_string_path_is_accepted(main_tree):
    """`locked_at` arrives as a string out of git's error message."""
    assert is_main_working_tree(str(main_tree), main_tree=main_tree) is True


# --- test-break: what git answers that is not an answer --------------------
#
# `main_working_tree` has one caller-visible contract — a Path, or None meaning
# "I could not look" — and `is_main_working_tree` turns None into True so that
# nothing is force-removed on a guess. Everything above tests the two ways git
# fails that `git_reading` already classifies: a non-zero exit, and a git that
# never ran. These cover the third, which it does not: git exits 0 and says
# something that is not a path. A bogus Path is worse than None here, because
# it compares UNEQUAL to the main working tree, and that is the answer that
# authorizes `git worktree remove --force` (AC1, S-2).


def _reading(stdout: str) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(args=["git"], returncode=0, stdout=stdout, stderr="")


@pytest.fixture(name="git_says")
def git_says_fixture():
    """Make git's read answer whatever a test needs, where `git_reading` looks."""
    from loregarden.services import worktree_snapshot

    def answering(stdout: str):
        return mock.patch.object(worktree_snapshot, "run_git", return_value=_reading(stdout))

    return answering


@pytest.mark.parametrize("stdout", ["", "\n", "   \n"], ids=["empty", "newline", "blank"])
def test_an_empty_answer_is_no_answer(main_tree, git_says, stdout):
    """`Path("")` resolves to the *process* working directory, whose parent is
    some unrelated tree. Carried into `is_main_working_tree` that answers False
    for the real main working tree — the one answer this ticket exists to stop.
    """
    with git_says(stdout):
        assert main_working_tree(main_tree) is None


def test_an_empty_answer_still_protects_the_main_tree(main_tree, git_says):
    """The same case through the guard, which is where it would do the damage."""
    with git_says(""):
        answer = main_working_tree(main_tree)

    assert is_main_working_tree(main_tree, main_tree=answer) is True


def test_a_relative_answer_is_resolved_against_the_tree_it_was_asked_in(main_tree, git_says):
    """S-1. `--path-format=absolute` is git >= 2.31, and the resolution against
    `tree` is there for a git that ignores it — as `shared_database_guard` has
    always done. Dropping it resolves the answer against *this process's* cwd.
    """
    with git_says(".git\n"):
        assert main_working_tree(main_tree) == main_tree.resolve()


def test_git_is_asked_once_with_the_flag_in_front_of_the_question(main_tree):
    """S-1. `--path-format=absolute` after `--git-common-dir` does not apply to
    it, and this read is on a per-dispatch path, so it is one process.
    """
    from loregarden.services import worktree_snapshot

    calls: list[tuple[list[str], Path | None]] = []
    real = worktree_snapshot.run_git

    def spy(args, **kwargs):
        calls.append((list(args), kwargs.get("cwd")))
        return real(args, **kwargs)

    with mock.patch.object(worktree_snapshot, "run_git", spy):
        main_working_tree(main_tree)

    assert [args for args, _ in calls] == [
        ["rev-parse", "--path-format=absolute", "--git-common-dir"]
    ]
    assert calls[0][1] == main_tree, "asked in the tree it was handed, not in this process's cwd"
