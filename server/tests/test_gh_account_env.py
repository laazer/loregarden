"""Every entry point acts as the `gh` account `.env` names, not the active one.

`dev-server.sh` sourced only its own checkout's `.env`, so a worktree server and
`loregarden-cli.sh mcp call` ran `gh` as the active account (pull-only `whjake`)
and four tree publishes failed `open_pr` on 2026-10-29..30.
"""

from __future__ import annotations

import os
from pathlib import Path
from unittest import mock

import pytest
from loregarden.services.gh_account_env import gh_user_from_env_file, prime_gh_user_env
from loregarden.services.git_subprocess import GH_USER_ENV, run_git


def _git(cwd: Path, *args: str) -> None:
    result = run_git(list(args), cwd=str(cwd), check=False, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


@pytest.fixture(name="checkouts")
def checkouts_fixture(tmp_path: Path) -> tuple[Path, Path]:
    """A primary checkout and a linked worktree of it, as loregarden runs them."""
    primary = tmp_path / "primary"
    primary.mkdir()
    _git(primary, "init", "-q", "-b", "main")
    _git(
        primary,
        "-c",
        "user.name=t",
        "-c",
        "user.email=t@t",
        "commit",
        "-q",
        "--allow-empty",
        "-m",
        "root",
    )
    worktree = tmp_path / "wt"
    _git(primary, "worktree", "add", "-q", "--detach", str(worktree))
    return primary, worktree


@pytest.fixture(name="no_gh_user")
def no_gh_user_fixture():
    env = {k: v for k, v in os.environ.items() if k != GH_USER_ENV}
    with mock.patch.dict(os.environ, env, clear=True):
        yield


def test_a_worktree_with_no_env_file_takes_the_primary_checkouts_account(checkouts, no_gh_user):
    primary, worktree = checkouts
    (primary / ".env").write_text(f"OTHER=1\n{GH_USER_ENV}=laazer\n", encoding="utf-8")

    assert prime_gh_user_env(repo_root=worktree) == "laazer"
    assert os.environ[GH_USER_ENV] == "laazer"


def test_the_checkouts_own_env_file_wins_over_the_primary(checkouts, no_gh_user):
    primary, worktree = checkouts
    (primary / ".env").write_text(f"{GH_USER_ENV}=laazer\n", encoding="utf-8")
    (worktree / ".env").write_text(f"{GH_USER_ENV}=someone-else\n", encoding="utf-8")

    assert prime_gh_user_env(repo_root=worktree) == "someone-else"


def test_an_explicit_environment_value_is_never_overridden(checkouts):
    primary, worktree = checkouts
    (primary / ".env").write_text(f"{GH_USER_ENV}=laazer\n", encoding="utf-8")

    with mock.patch.dict(os.environ, {GH_USER_ENV: "from-env"}):
        assert prime_gh_user_env(repo_root=worktree) == "from-env"
        assert os.environ[GH_USER_ENV] == "from-env"


def test_nothing_named_leaves_the_setting_unset(checkouts, no_gh_user):
    _primary, worktree = checkouts

    assert prime_gh_user_env(repo_root=worktree) is None
    assert GH_USER_ENV not in os.environ


def test_only_the_gh_user_key_is_exported(checkouts, no_gh_user):
    """Sourcing the whole file would hand an agent's CLI call the operator's
    local overrides; only the account is primed."""
    primary, worktree = checkouts
    (primary / ".env").write_text(
        f"LOREGARDEN_ALLOW_PERMISSION_BYPASS=1\n{GH_USER_ENV}=laazer\n", encoding="utf-8"
    )
    env = {k: v for k, v in os.environ.items() if k != "LOREGARDEN_ALLOW_PERMISSION_BYPASS"}

    with mock.patch.dict(os.environ, env, clear=True):
        prime_gh_user_env(repo_root=worktree)
        assert "LOREGARDEN_ALLOW_PERMISSION_BYPASS" not in os.environ


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        (f"{GH_USER_ENV}=laazer", "laazer"),
        (f"export {GH_USER_ENV}=laazer", "laazer"),
        (f'{GH_USER_ENV}="laazer"', "laazer"),
        (f"{GH_USER_ENV}='laazer'  # the writer", "laazer"),
        (f"  {GH_USER_ENV}=laazer", "laazer"),
        (f"{GH_USER_ENV} = laazer", None),
        (f"# {GH_USER_ENV}=commented-out", None),
        (f"NOT_{GH_USER_ENV}=x", None),
    ],
)
def test_the_assignment_is_read_as_the_shell_reads_it(tmp_path, line, expected):
    env_file = tmp_path / ".env"
    env_file.write_text(line + "\n", encoding="utf-8")

    assert gh_user_from_env_file(env_file) == expected


def test_the_last_assignment_wins_as_when_sourced(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text(f"{GH_USER_ENV}=first\n{GH_USER_ENV}=second\n", encoding="utf-8")

    assert gh_user_from_env_file(env_file) == "second"


def test_an_unparseable_value_is_skipped_not_raised(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text(f'{GH_USER_ENV}="unterminated\n', encoding="utf-8")

    assert gh_user_from_env_file(env_file) is None


def test_a_missing_file_names_no_account(tmp_path):
    assert gh_user_from_env_file(tmp_path / ".env") is None
