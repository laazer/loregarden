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
import time
from pathlib import Path

import pytest
import yaml
from loregarden.services.git_subprocess import SERVER_GIT_ENV, run_git

REPO = Path(__file__).resolve().parents[2]
SCRIPTS = REPO / ".lefthook" / "scripts"
NULL_REV = "0" * 40
BOOTSTRAP = REPO / ".claude" / "hooks" / "worktree_bootstrap.sh"


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

    def sync(
        self, *args: str, cwd: Path | None = None, extra_env: dict[str, str] | None = None
    ) -> subprocess.CompletedProcess:
        tree = cwd or self.root
        env = {
            k: v
            for k, v in os.environ.items()
            if k not in {"GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", SERVER_GIT_ENV}
        }
        env["PATH"] = f"{self.bin_dir}{os.pathsep}{env['PATH']}"
        env.update(extra_env or {})
        return subprocess.run(
            ["bash", str(tree / ".lefthook" / "scripts" / "sync-deps.sh"), *args],
            cwd=tree,
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
    (root / ".claude" / "hooks").mkdir(parents=True)
    shutil.copy(BOOTSTRAP, root / ".claude" / "hooks" / BOOTSTRAP.name)
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
        # The primary checkout's own dependencies are the operator's.
        ("a clone starts from the null revision", True, "1"),
    ],
)
def test_checkouts_that_are_not_a_branch_move_install_nothing(
    repo: Repo, label: str, old_is_null: bool, flag: str
):
    old = repo.git("rev-parse", "HEAD")
    new = repo.commit("server/uv.lock", "lock v2\n")

    assert repo.sync("checkout", NULL_REV if old_is_null else old, new, flag).returncode == 0, label
    assert repo.installs() == [], label


@pytest.fixture
def worktree(repo: Repo, tmp_path: Path) -> Path:
    tree = tmp_path / "linked"
    repo.git("worktree", "add", "-q", "-b", "side", str(tree))
    return tree


def _bootstrap_log(tree: Path) -> Path:
    gitdir = run_git(
        ["rev-parse", "--absolute-git-dir"], cwd=tree, check=True, capture_output=True, text=True
    ).stdout.strip()
    return Path(gitdir) / "worktree-bootstrap.log"


def _wait_for_bootstrap(tree: Path) -> str:
    log = _bootstrap_log(tree)
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        text = log.read_text(encoding="utf-8") if log.exists() else ""
        if "done" in text or "FAILED" in text:
            return text
        time.sleep(0.1)
    raise AssertionError(f"the bootstrap did not finish: {log}")


def test_a_worktree_made_in_a_shell_installs_what_it_lacks(repo: Repo, worktree: Path):
    result = repo.sync("checkout", NULL_REV, "HEAD", "1", cwd=worktree)

    assert result.returncode == 0, result.stderr
    assert "installing it in the background" in result.stdout
    assert "done" in _wait_for_bootstrap(worktree)
    assert sorted(repo.installs()) == ["npm", "uv"]


def test_a_worktree_the_server_made_is_left_to_the_server(repo: Repo, worktree: Path):
    result = repo.sync(
        "checkout", NULL_REV, "HEAD", "1", cwd=worktree, extra_env={SERVER_GIT_ENV: "1"}
    )

    assert result.returncode == 0, result.stderr
    assert not _bootstrap_log(worktree).exists()
    assert repo.installs() == []


def test_every_server_git_command_carries_the_marker(tmp_path: Path):
    # A shell alias runs in the environment git was given: what a hook would see.
    probe = run_git(
        ["-c", f"alias.marker=!printenv {SERVER_GIT_ENV}", "marker"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=True,
    )

    assert probe.stdout.strip() == "1"


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
