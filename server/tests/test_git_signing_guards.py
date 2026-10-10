"""Unsigned commits cannot be made by an agent, or pushed by anyone.

Two agent runs on lg-durable-remote-336 committed with
`git -c commit.gpgsign=false commit`, unprompted, and the unsigned commits
reached PR #555. The PreToolUse guard denies the command; the pre-push gate
refuses the commit however it was made.
"""

from __future__ import annotations

import importlib.util
import shutil
import subprocess
from pathlib import Path

import pytest
from loregarden.services.git_subprocess import scrubbed_git_env

_ROOT = Path(__file__).resolve().parents[2]
_GUARD = _ROOT / ".claude" / "hooks" / "git_signing_guard.py"
_GATE = _ROOT / ".lefthook" / "scripts" / "signed-commits.sh"
_spec = importlib.util.spec_from_file_location("git_signing_guard", _GUARD)
guard = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(guard)

ZERO = "0" * 40


def _decide(command: str):
    return guard.decide({"tool_input": {"command": command}})


# --- the PreToolUse guard --------------------------------------------------------


@pytest.mark.parametrize(
    "command",
    [
        "git -c commit.gpgsign=false commit -q -F - <<'MSG'\nx\nMSG",  # the run_9931e9 form
        "cd /w && git add -A && git -c commit.gpgsign=false commit -F - <<'MSG' 2>&1",
        "git -c commit.gpgSign=0 commit -m x",
        "git -c 'commit.gpgsign=no' commit -m x",
        "git commit --no-gpg-sign -m x",
        "git rebase --exec 'git commit --amend --no-edit --no-gpg-sign' main",
        "git config commit.gpgsign false && git commit -m x",
        "git config --local commit.gpgsign off",
        "git config --unset commit.gpgsign",
        "GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=commit.gpgsign GIT_CONFIG_VALUE_0=false git commit -m x",
    ],
)
def test_turning_signing_off_is_denied(command):
    verdict = _decide(command)
    assert verdict is not None and verdict[0] == "deny"


@pytest.mark.parametrize(
    "command",
    [
        "git commit -m 'sign the form'",
        "git commit -S -m x",
        "git config commit.gpgsign true",
        "git log --show-signature -1",
        "echo commit.gpgsign=false",  # not a git command
    ],
)
def test_ordinary_git_passes(command):
    assert _decide(command) is None


# --- the pre-push gate -----------------------------------------------------------


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True, env=scrubbed_git_env()
    ).stdout.strip()


@pytest.fixture(name="repo")
def repo_fixture(tmp_path: Path) -> Path:
    """A repository that signs with a throwaway SSH key, pushed once to a bare origin."""
    if shutil.which("ssh-keygen") is None:
        pytest.skip("ssh-keygen is needed to make a signing key")
    key = tmp_path / "signing_key"
    subprocess.run(
        ["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(key)],
        check=True,
        capture_output=True,
    )
    origin = tmp_path / "origin.git"
    _git(tmp_path, "init", "-q", "--bare", "-b", "main", str(origin))
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    for name, value in (
        ("user.email", "t@example.com"),
        ("user.name", "T"),
        ("gpg.format", "ssh"),
        ("user.signingkey", f"{key}.pub"),
        ("commit.gpgsign", "true"),
    ):
        _git(repo, "config", name, value)
    _git(repo, "remote", "add", "origin", str(origin))
    (repo / "seed").write_text("seed\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "seed")
    _git(repo, "push", "-q", "origin", "main")
    return repo


def _commit(repo: Path, name: str, *, signed: bool) -> str:
    (repo / name).write_text(f"{name}\n")
    _git(repo, "add", ".")
    flags = [] if signed else ["-c", "commit.gpgsign=false"]
    _git(repo, *flags, "commit", "-q", "-m", name)
    return _git(repo, "rev-parse", "HEAD")


def _gate(repo: Path, local_sha: str, remote_sha: str) -> subprocess.CompletedProcess[str]:
    stdin = f"refs/heads/main {local_sha} refs/heads/main {remote_sha}\n"
    return subprocess.run(
        ["bash", str(_GATE)],
        cwd=repo,
        input=stdin,
        capture_output=True,
        text=True,
        env=scrubbed_git_env(),
    )


def test_signed_commits_push(repo):
    base = _git(repo, "rev-parse", "HEAD")
    head = _commit(repo, "signed", signed=True)
    assert _gate(repo, head, base).returncode == 0


def test_an_unsigned_commit_is_refused_and_named(repo):
    base = _git(repo, "rev-parse", "HEAD")
    _commit(repo, "fine", signed=True)
    head = _commit(repo, "sneaky", signed=False)

    result = _gate(repo, head, base)

    assert result.returncode == 1
    assert "sneaky" in result.stderr
    assert "fine" not in result.stderr


def test_a_new_branch_checks_only_what_the_remote_lacks(repo):
    """The seed is on origin already; only the new commit is checked."""
    _git(repo, "switch", "-q", "-c", "feature")
    head = _commit(repo, "feature-work", signed=False)
    result = _gate(repo, head, ZERO)
    assert result.returncode == 1
    assert "feature-work" in result.stderr and "seed" not in result.stderr


def test_a_repository_that_does_not_sign_is_left_alone(repo):
    _git(repo, "config", "commit.gpgsign", "false")
    base = _git(repo, "rev-parse", "HEAD")
    head = _commit(repo, "plain", signed=False)
    assert _gate(repo, head, base).returncode == 0


def test_a_deletion_pushes_nothing_to_check(repo):
    assert _gate(repo, ZERO, _git(repo, "rev-parse", "HEAD")).returncode == 0
