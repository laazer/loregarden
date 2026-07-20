"""The shared git-subprocess chokepoint scrubs inherited repo bindings."""

import os
import subprocess
from pathlib import Path

from loregarden.models.domain import Ticket, WorkItemType
from loregarden.services.git_branch import ensure_ticket_branch
from loregarden.services.git_subprocess import (
    GIT_LOCATION_ENV_VARS,
    run_git,
    scrubbed_git_env,
)


def _init_repo(path: Path) -> None:
    subprocess.run(["git", "init", "-b", "main"], cwd=path, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"],
        cwd=path,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"], cwd=path, check=True, capture_output=True
    )
    (path / "README.md").write_text("# test\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=path, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=path, check=True, capture_output=True)


def _two_repos(tmp_path: Path) -> tuple[Path, Path]:
    """A repo to operate on, and an unrelated one to leak a GIT_DIR from."""
    target = tmp_path / "target"
    intruder = tmp_path / "intruder"
    for repo in (target, intruder):
        repo.mkdir()
        _init_repo(repo)
    return target, intruder


def test_scrubbed_git_env_drops_repo_bindings():
    env = scrubbed_git_env({"GIT_DIR": "/elsewhere/.git", "PATH": "/usr/bin", "HOME": "/home/x"})
    assert "GIT_DIR" not in env
    # Everything unrelated survives — the child still needs a usable environment.
    assert env["PATH"] == "/usr/bin"
    assert env["HOME"] == "/home/x"


def test_scrubbed_git_env_drops_every_location_var():
    polluted = dict.fromkeys(GIT_LOCATION_ENV_VARS, "/elsewhere")
    assert scrubbed_git_env(polluted) == {}


def test_scrubbed_git_env_defaults_to_ambient_environment(monkeypatch):
    monkeypatch.setenv("GIT_DIR", "/elsewhere/.git")
    assert "GIT_DIR" not in scrubbed_git_env()
    # The caller's own environment is left intact.
    assert os.environ["GIT_DIR"] == "/elsewhere/.git"


def test_inherited_git_dir_would_hijack_a_plain_subprocess(tmp_path, monkeypatch):
    """Guard the premise: without scrubbing, GIT_DIR really does beat cwd.

    If this ever stops holding, the tests below pass for the wrong reason.
    """
    target, intruder = _two_repos(tmp_path)
    monkeypatch.setenv("GIT_DIR", str(intruder / ".git"))

    proc = subprocess.run(
        ["git", "rev-parse", "--absolute-git-dir"],
        cwd=target,
        capture_output=True,
        text=True,
    )
    assert Path(proc.stdout.strip()).resolve() == (intruder / ".git").resolve()


def test_run_git_resolves_through_cwd_despite_inherited_git_dir(tmp_path, monkeypatch):
    target, intruder = _two_repos(tmp_path)
    monkeypatch.setenv("GIT_DIR", str(intruder / ".git"))
    monkeypatch.setenv("GIT_WORK_TREE", str(intruder))

    proc = run_git(
        ["rev-parse", "--absolute-git-dir"],
        cwd=target,
        capture_output=True,
        text=True,
    )
    assert Path(proc.stdout.strip()).resolve() == (target / ".git").resolve()


def test_run_git_honours_dash_c_despite_inherited_git_dir(tmp_path, monkeypatch):
    """The `_git(cwd, *args)` service helpers select the repo with `-C`, not cwd."""
    target, intruder = _two_repos(tmp_path)
    monkeypatch.setenv("GIT_DIR", str(intruder / ".git"))

    proc = run_git(
        ["-C", str(target), "rev-parse", "--absolute-git-dir"],
        capture_output=True,
        text=True,
    )
    assert Path(proc.stdout.strip()).resolve() == (target / ".git").resolve()


def test_run_git_writes_to_the_repo_at_cwd(tmp_path, monkeypatch):
    """A mutating command lands in the target repo, not the leaked one."""
    target, intruder = _two_repos(tmp_path)
    monkeypatch.setenv("GIT_DIR", str(intruder / ".git"))

    (target / "new.txt").write_text("x\n", encoding="utf-8")
    run_git(["add", "."], cwd=target, check=True, capture_output=True)
    run_git(["commit", "-m", "scoped"], cwd=target, check=True, capture_output=True)

    def _subject(repo: Path) -> str:
        return subprocess.run(
            ["git", "log", "-1", "--format=%s"],
            cwd=repo,
            env={k: v for k, v in os.environ.items() if k not in GIT_LOCATION_ENV_VARS},
            capture_output=True,
            text=True,
        ).stdout.strip()

    assert _subject(target) == "scoped"
    assert _subject(intruder) == "init"


def test_service_layer_branch_checkout_survives_leaked_git_dir(tmp_path, monkeypatch):
    """End-to-end: a real service call routed through the helper stays scoped."""
    target, intruder = _two_repos(tmp_path)
    monkeypatch.setenv("GIT_DIR", str(intruder / ".git"))

    ticket = Ticket(
        external_id="99-scoped",
        work_item_type=WorkItemType.TASK,
        title="x",
        workspace_id="w",
    )
    branch = ensure_ticket_branch(target, ticket)
    assert branch == "loregarden/99-scoped"

    def _current_branch(repo: Path) -> str:
        return subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=repo,
            env={k: v for k, v in os.environ.items() if k not in GIT_LOCATION_ENV_VARS},
            capture_output=True,
            text=True,
        ).stdout.strip()

    assert _current_branch(target) == "loregarden/99-scoped"
    # The leaked repo was never touched.
    assert _current_branch(intruder) == "main"
