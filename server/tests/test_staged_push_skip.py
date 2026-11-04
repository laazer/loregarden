"""The pre-push suites skip only a merge-queue staging push (lg-milestone-that-967).

`.lefthook/scripts/staged-push.sh` is a lefthook `skip` condition: exit 0 skips
the suite. It must skip exactly one shape — the commit the push names, cut as a
single commit on the fetched remote base's tip — and run the suite for every
other push, including one that sets the variable on an ordinary commit.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest
from tests.worktree_helpers import commit_on, git, make_repo

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / ".lefthook" / "scripts" / "staged-push.sh"


@pytest.fixture(name="repo")
def repo_fixture(tmp_path):
    """A clone whose origin/main is fetched, with the script at its usual path."""
    repo = make_repo(tmp_path)
    remote = tmp_path / "remote.git"
    git(tmp_path, "init", "-q", "--bare", str(remote))
    git(repo, "remote", "add", "origin", str(remote))
    git(repo, "push", "-q", "-u", "origin", "main")
    scripts = repo / ".lefthook" / "scripts"
    scripts.mkdir(parents=True)
    shutil.copy(SCRIPT, scripts / SCRIPT.name)
    return repo


def _skips(repo: Path, **env: str) -> bool:
    scrubbed = {
        k: v for k, v in os.environ.items() if not k.startswith(("LOREGARDEN_PUSH_", "GIT_"))
    }
    result = subprocess.run(
        ["bash", str(repo / ".lefthook" / "scripts" / SCRIPT.name), "server-tests"],
        env=scrubbed | env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode in (0, 1), result.stderr
    return result.returncode == 0


def test_an_ordinary_push_runs_the_suite(repo):
    assert not _skips(repo)


def test_a_commit_cut_on_the_bases_tip_skips_it(repo):
    git(repo, "branch", "publish/x", "main")
    staged = commit_on(repo, "publish/x", "work.txt", "work\n")

    assert _skips(repo, LOREGARDEN_PUSH_STAGED=staged, LOREGARDEN_PUSH_STAGED_BASE="main")


def test_the_bases_tip_itself_runs_the_suite(repo):
    tip = git(repo, "rev-parse", "origin/main").stdout.strip()

    assert not _skips(repo, LOREGARDEN_PUSH_STAGED=tip)


def test_a_commit_behind_the_moved_base_runs_the_suite(repo):
    """Stale: the base moved after the cut, so CI's commit is not this one's tree."""
    git(repo, "branch", "publish/x", "main")
    staged = commit_on(repo, "publish/x", "work.txt", "work\n")
    commit_on(repo, "main", "other.txt", "other\n")
    git(repo, "push", "-q", "origin", "main")

    assert not _skips(repo, LOREGARDEN_PUSH_STAGED=staged)


def test_a_name_that_is_not_a_commit_runs_the_suite(repo):
    assert not _skips(repo, LOREGARDEN_PUSH_STAGED="f" * 40)
