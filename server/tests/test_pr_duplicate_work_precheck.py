"""The PreToolUse hook that refuses a PR for work already on an integration branch.

lg-durable-remote-336 was landed on `integration/lg-durable-remote-335` and also
opened by hand as PR #555, so one conflict with `main` had to be resolved twice.
"""

from __future__ import annotations

import importlib.util
import itertools
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from loregarden.services.git_subprocess import scrubbed_git_env

_ROOT = Path(__file__).resolve().parents[2]
_HOOK = _ROOT / ".claude" / "hooks" / "pr_duplicate_work_precheck.py"
_spec = importlib.util.spec_from_file_location("pr_duplicate_work_precheck", _HOOK)
hook = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(hook)


_clock = itertools.count(1_700_000_000, 60)


def _git(cwd: Path, *args: str) -> None:
    # Strictly increasing commit dates: `git merge-base` breaks ties between
    # criss-cross bases by date, and commits made within one second would make
    # the test's answer depend on the machine's speed.
    stamp = f"{next(_clock)} +0000"
    env = {**scrubbed_git_env(), "GIT_AUTHOR_DATE": stamp, "GIT_COMMITTER_DATE": stamp}
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, env=env)


def _commit(repo: Path, name: str) -> None:
    (repo / name).write_text(f"{name}\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", name)


@pytest.fixture(name="repo")
def repo_fixture(tmp_path: Path) -> Path:
    """main; a child branch landed on integration/root; an unrelated feature."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "T")
    _commit(repo, "seed")
    _git(repo, "branch", "integration/root")
    _git(repo, "switch", "-q", "-c", "loregarden/child", "integration/root")
    _commit(repo, "child-work")
    _git(repo, "switch", "-q", "integration/root")
    _git(repo, "merge", "-q", "--no-ff", "loregarden/child", "-m", "land child")
    _git(repo, "switch", "-q", "-c", "feature", "main")
    _commit(repo, "feature-work")
    _git(repo, "switch", "-q", "main")
    return repo


def _decide(command: str, cwd: Path):
    return hook.decide({"tool_input": {"command": command}, "cwd": str(cwd)})


def test_a_landed_child_branch_is_refused(repo):
    verdict = _decide("gh pr create --head loregarden/child --title t --body b", repo)
    assert verdict is not None and verdict[0] == "deny"
    assert "integration/root" in verdict[1]


def test_still_refused_after_the_child_takes_main_and_gains_commits(repo):
    """Both sides have main now, so plain `merge-base` answers main's tip."""
    _commit(repo, "main-moved")
    _git(repo, "switch", "-q", "loregarden/child")
    _git(repo, "merge", "-q", "--no-edit", "main")
    _commit(repo, "more-child-work")
    _git(repo, "switch", "-q", "integration/root")
    _git(repo, "merge", "-q", "--no-edit", "main")

    _git(repo, "switch", "-q", "loregarden/child")
    assert _decide("gh pr create --title t --body b", repo)[0] == "deny"


def test_the_trees_own_pr_and_unrelated_work_pass(repo):
    assert _decide("gh pr create --head integration/root --title t --body b", repo) is None
    assert _decide("gh pr create --head feature --title t --body b", repo) is None
    assert _decide("gh pr view 1", repo) is None


def test_an_unknown_branch_passes_with_a_note_not_silently(repo):
    verdict = _decide("gh pr create --head nope --title t --body b", repo)
    assert verdict is not None and verdict[0] is None
    assert "did not run" in verdict[1]


def test_the_script_speaks_the_hook_protocol(repo):
    """End to end, under the interpreter hooks actually get."""
    payload = json.dumps(
        {
            "tool_input": {"command": "gh pr create --head loregarden/child --title t --body b"},
            "cwd": str(repo),
        }
    )
    python = shutil.which("python3") or sys.executable
    out = subprocess.run(
        [python, str(_HOOK)], input=payload, capture_output=True, text=True, check=True
    )
    decision = json.loads(out.stdout)["hookSpecificOutput"]
    assert decision["permissionDecision"] == "deny"
