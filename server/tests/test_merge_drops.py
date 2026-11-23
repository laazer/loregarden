"""A merge may not quietly delete a file only its own branch added.

`8ee19bc68` resolved a conflict on integration/lg-milestone-that-491 by deleting
the six files lg-milestone-that-1316 had landed there, and every ancestry check
went on reading the work as held (lg-milestone-that-2622).
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest
from tests.repo_paths import REPO_ROOT
from tests.worktree_helpers import commit_on, git, make_repo

SCRIPT = REPO_ROOT / ".lefthook" / "scripts" / "merge_drops_check.py"
BRANCH = "integration/ms"


def _check(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--repo", str(repo), *args],
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.fixture(name="repo")
def repo_fixture(tmp_path):
    """main and a branch that added `landed.txt` since they parted, each with a change."""
    repo = make_repo(tmp_path)
    git(repo, "config", "core.hooksPath", "/dev/null")
    git(repo, "branch", BRANCH, "main")
    commit_on(repo, BRANCH, "landed.txt", "the branch's work\n")
    commit_on(repo, "main", "shipped.txt", "main's work\n")
    git(repo, "checkout", "-q", BRANCH)
    return repo


def _merging(repo: Path) -> None:
    git(repo, "merge", "--no-ff", "--no-commit", "main")


def _message(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "COMMIT_EDITMSG"
    path.write_text(text)
    return path


# --- a merge concluded by hand (commit-msg) ----------------------------------------------


def test_a_resolution_that_deletes_the_branchs_own_file_is_refused(repo, tmp_path):
    _merging(repo)
    git(repo, "rm", "-q", "landed.txt")

    result = _check(repo, "--msg-file", str(_message(tmp_path, "Merge main\n")))

    assert result.returncode == 1
    assert " - landed.txt" in result.stdout


def test_a_drop_the_message_declares_with_a_reason_passes(repo, tmp_path):
    _merging(repo)
    git(repo, "rm", "-q", "landed.txt")
    message = "Merge main\n\nDrops: landed.txt — main's design replaces it outright\n"

    assert _check(repo, "--msg-file", str(_message(tmp_path, message))).returncode == 0


def test_a_declaration_with_no_real_reason_does_not_count(repo, tmp_path):
    _merging(repo)
    git(repo, "rm", "-q", "landed.txt")

    result = _check(
        repo, "--msg-file", str(_message(tmp_path, "Merge\n\nDrops: landed.txt - ok\n"))
    )

    assert result.returncode == 1


def test_a_declaration_in_a_comment_does_not_count(repo, tmp_path):
    _merging(repo)
    git(repo, "rm", "-q", "landed.txt")
    message = "Merge main\n# Drops: landed.txt — main's design replaces it outright\n"

    assert _check(repo, "--msg-file", str(_message(tmp_path, message))).returncode == 1


def test_a_merge_that_keeps_both_sides_passes(repo, tmp_path):
    _merging(repo)

    assert _check(repo, "--msg-file", str(_message(tmp_path, "Merge main\n"))).returncode == 0


def test_an_ordinary_commit_deleting_a_file_is_not_this_checks_business(repo, tmp_path):
    git(repo, "rm", "-q", "landed.txt")

    assert _check(repo, "--msg-file", str(_message(tmp_path, "Remove it\n"))).returncode == 0


# --- a merge commit already written (the merge turn) -------------------------------------


def _commit_merge(repo: Path) -> str:
    git(repo, "commit", "-q", "-m", "Merge main")
    return git(repo, "rev-parse", "HEAD").stdout.strip()


def test_a_merge_commit_that_deleted_the_branchs_file_names_it(repo):
    _merging(repo)
    git(repo, "rm", "-q", "landed.txt")

    result = _check(repo, "--commit", _commit_merge(repo))

    assert (result.returncode, result.stdout.split()) == (1, ["-", "landed.txt"])


def test_a_rename_is_not_a_deletion(repo):
    _merging(repo)
    git(repo, "mv", "landed.txt", "moved.txt")

    assert _check(repo, "--commit", _commit_merge(repo)).returncode == 0


def test_deleting_a_file_both_sides_started_with_is_not_a_drop_of_branch_work(repo):
    _merging(repo)
    git(repo, "rm", "-q", "seed.txt")

    assert _check(repo, "--commit", _commit_merge(repo)).returncode == 0


def test_a_commit_that_is_not_a_merge_passes(repo):
    git(repo, "rm", "-q", "landed.txt")
    git(repo, "commit", "-q", "-m", "Remove it")

    assert _check(repo, "--commit", "HEAD").returncode == 0


def test_a_commit_git_cannot_read_is_unavailable_not_a_pass(repo):
    result = _check(repo, "--commit", "0" * 40)

    assert result.returncode == 69
