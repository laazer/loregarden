"""A pull, merge, rebase or branch switch that moves a lockfile re-syncs dependencies.

#932 bumped lore-eden in server/uv.lock; the primary checkout pulled it, kept
the old venv, and the dev server's next reload died at import. The post-merge,
post-checkout and post-rewrite hooks run `.lefthook/scripts/sync-deps.sh`, which
must sync exactly when a lockfile moved and stay out of the way otherwise.
These run the real script in a scratch repository with `uv` and `npm` stubbed.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml
from loregarden.services.git_subprocess import run_git

REPO = Path(__file__).resolve().parents[2]
SCRIPTS = REPO / ".lefthook" / "scripts"
NULL_REV = "0" * 40


class Repo:
    def __init__(self, root: Path, calls: Path, bin_dir: Path) -> None:
        self.root = root
        self.calls = calls
        self.bin_dir = bin_dir

    def git(self, *args: str) -> str:
        return run_git(
            list(args), cwd=self.root, check=True, capture_output=True, text=True
        ).stdout.strip()

    def commit(self, path: str, text: str) -> str:
        target = self.root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
        self.git("add", path)
        self.git("commit", "-q", "-m", f"edit {path}")
        return self.git("rev-parse", "HEAD")

    def sync(self, *args: str) -> subprocess.CompletedProcess:
        env = {
            k: v
            for k, v in os.environ.items()
            if k not in {"GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE"}
        }
        env["PATH"] = f"{self.bin_dir}{os.pathsep}{env['PATH']}"
        return subprocess.run(
            ["bash", str(self.root / ".lefthook" / "scripts" / "sync-deps.sh"), *args],
            cwd=self.root,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )

    def installs(self) -> list[str]:
        return self.calls.read_text(encoding="utf-8").split() if self.calls.exists() else []


def _stub(bin_dir: Path, name: str, calls: Path, exit_code: int = 0) -> None:
    stub = bin_dir / name
    stub.write_text(f'#!/bin/sh\necho {name} >> "{calls}"\nexit {exit_code}\n', encoding="utf-8")
    stub.chmod(0o755)


@pytest.fixture
def repo(tmp_path: Path) -> Repo:
    root = tmp_path / "repo"
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    calls = tmp_path / "calls"
    _stub(bin_dir, "uv", calls)
    _stub(bin_dir, "npm", calls)
    (root / ".lefthook" / "scripts").mkdir(parents=True)
    for script in ("sync-deps.sh", "ensure-node.sh"):
        shutil.copy(SCRIPTS / script, root / ".lefthook" / "scripts" / script)
    run_git(["init", "-q", "-b", "main", str(root)], check=True, capture_output=True)
    made = Repo(root, calls, bin_dir)
    made.git("config", "user.email", "test@example.invalid")
    made.git("config", "user.name", "test")
    made.git("config", "commit.gpgsign", "false")
    made.git("add", ".")
    made.git("commit", "-q", "-m", "scripts")
    made.commit("server/uv.lock", "lock v1\n")
    made.commit("client/package-lock.json", "{}\n")
    return made


def test_a_merge_that_moves_uv_lock_runs_uv_sync(repo: Repo):
    old = repo.git("rev-parse", "HEAD")
    repo.commit("server/uv.lock", "lock v2\n")
    repo.git("update-ref", "ORIG_HEAD", old)

    result = repo.sync("merge")

    assert result.returncode == 0, result.stderr
    assert repo.installs() == ["uv"]


def test_a_merge_that_moves_package_lock_runs_npm_ci(repo: Repo):
    old = repo.git("rev-parse", "HEAD")
    repo.commit("client/package-lock.json", '{"v": 2}\n')
    repo.git("update-ref", "ORIG_HEAD", old)

    result = repo.sync("merge")

    assert result.returncode == 0, result.stderr
    assert repo.installs() == ["npm"]


def test_a_merge_that_moves_no_lockfile_installs_nothing(repo: Repo):
    old = repo.git("rev-parse", "HEAD")
    repo.commit("README.md", "hi\n")
    repo.git("update-ref", "ORIG_HEAD", old)

    assert repo.sync("merge").returncode == 0
    assert repo.installs() == []


def test_a_branch_switch_across_a_lock_change_syncs(repo: Repo):
    old = repo.git("rev-parse", "HEAD")
    new = repo.commit("server/pyproject.toml", "[project]\n")

    assert repo.sync("checkout", old, new, "1").returncode == 0
    assert repo.installs() == ["uv"]


@pytest.mark.parametrize(
    ("label", "old_is_null", "flag"),
    [
        ("a file checkout moves no branch", False, "0"),
        ("worktree add / clone start from the null revision", True, "1"),
    ],
)
def test_checkouts_that_are_not_a_branch_move_install_nothing(
    repo: Repo, label: str, old_is_null: bool, flag: str
):
    old = repo.git("rev-parse", "HEAD")
    new = repo.commit("server/uv.lock", "lock v2\n")

    assert repo.sync("checkout", NULL_REV if old_is_null else old, new, flag).returncode == 0, label
    assert repo.installs() == [], label


def _rebase_in_progress(repo: Repo) -> None:
    (Path(repo.git("rev-parse", "--absolute-git-dir")) / "rebase-merge").mkdir()


def test_checkouts_inside_a_rebase_wait_for_post_rewrite(repo: Repo):
    old = repo.git("rev-parse", "HEAD")
    new = repo.commit("server/uv.lock", "lock v2\n")
    _rebase_in_progress(repo)

    assert repo.sync("checkout", old, new, "1").returncode == 0
    assert repo.installs() == []


def test_post_rewrite_syncs_a_rebase_while_git_still_holds_the_rebase_dir(repo: Repo):
    # git runs post-rewrite before it removes .git/rebase-merge; a guard on that
    # directory once swallowed the one call meant to sync.
    old = repo.git("rev-parse", "HEAD")
    repo.commit("server/uv.lock", "lock v2\n")
    repo.git("update-ref", "ORIG_HEAD", old)
    _rebase_in_progress(repo)

    assert repo.sync("rewrite", "rebase").returncode == 0
    assert repo.installs() == ["uv"]


def test_an_amend_installs_nothing(repo: Repo):
    old = repo.git("rev-parse", "HEAD")
    repo.commit("server/uv.lock", "lock v2\n")
    repo.git("update-ref", "ORIG_HEAD", old)

    assert repo.sync("rewrite", "amend").returncode == 0
    assert repo.installs() == []


def test_a_failed_install_exits_non_zero_and_names_the_command(repo: Repo):
    _stub(repo.bin_dir, "uv", repo.calls, exit_code=1)
    old = repo.git("rev-parse", "HEAD")
    repo.commit("server/uv.lock", "lock v2\n")
    repo.git("update-ref", "ORIG_HEAD", old)

    result = repo.sync("merge")

    assert result.returncode == 1
    assert "uv sync" in result.stderr


def test_lefthook_runs_the_script_from_each_hook_with_its_arguments():
    config = yaml.safe_load((REPO / "lefthook.yml").read_text(encoding="utf-8"))
    expected = {
        "post-merge": "bash .lefthook/scripts/sync-deps.sh merge",
        "post-checkout": "bash .lefthook/scripts/sync-deps.sh checkout {1} {2} {3}",
        "post-rewrite": "bash .lefthook/scripts/sync-deps.sh rewrite {1}",
    }
    for hook, run in expected.items():
        command = config[hook]["commands"]["sync-deps"]
        assert command["run"] == run, hook
        # lefthook does not substitute {N} in `skip`; a condition there never
        # sees the hook's arguments (post-checkout always skipped that way).
        assert "skip" not in command, hook
