"""`open_change_pr`: a change in another repo, as a PR, without touching its checkout.

Real git against a local bare "origin"; only `gh` is replaced. The checkout
under test is left dirty and on a non-default branch on purpose — that is the
state a workspace is in when someone needs to change it.
"""

import subprocess
import sys
from pathlib import Path
from unittest import mock

import pytest
from loregarden.cli import git_tools
from loregarden.cli import main as cli_main
from loregarden.cli.errors import EXIT_ERROR, EXIT_OK, EXIT_USAGE
from loregarden.services import change_pr
from loregarden.services.change_pr import ChangeOutcome, ChangePrError, ChangeRequest
from loregarden.services.git_subprocess import scrubbed_git_env

_PR_URL = "https://github.com/o/r/pull/7"


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, env=scrubbed_git_env(), capture_output=True, text=True, check=True
    ).stdout.strip()


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A clone whose origin's default branch is `master`, checked out elsewhere and dirty."""
    origin = tmp_path / "origin.git"
    _git(tmp_path, "init", "-q", "--bare", "-b", "master", str(origin))
    seed = tmp_path / "seed"
    _git(tmp_path, "init", "-q", "-b", "master", str(seed))
    for key, value in (("user.email", "t@example.com"), ("user.name", "t")):
        _git(seed, "config", key, value)
    (seed / "lefthook.yml").write_text("old\n")
    _git(seed, "add", ".")
    _git(seed, "commit", "-qm", "init")
    _git(seed, "push", "-q", str(origin), "master")

    # `init` + `remote add`, not `clone`: no refs/remotes/origin/HEAD, as in loremaker.
    checkout = tmp_path / "checkout"
    _git(tmp_path, "init", "-q", "-b", "master", str(checkout))
    for key, value in (("user.email", "t@example.com"), ("user.name", "t")):
        _git(checkout, "config", key, value)
    _git(checkout, "remote", "add", "origin", str(origin))
    _git(checkout, "fetch", "-q", "origin")
    _git(checkout, "checkout", "-q", "-b", "feature", "origin/master")
    (checkout / "lefthook.yml").write_text("someone's uncommitted edit\n")
    return checkout


def _request(repo: Path, command: list[str], branch: str = "chore/hooks") -> ChangeRequest:
    return ChangeRequest(repo, branch, "Refresh hooks", "Body.", command)


def _gh_ok(*_args, **_kwargs) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess([], 0, stdout=f"{_PR_URL}\n", stderr="")


_WRITE = [sys.executable, "-c", "open('{worktree}/lefthook.yml', 'w').write('new\\n')"]


def test_opens_a_pr_from_the_remote_default_branch(repo: Path):
    with mock.patch.object(change_pr, "run_gh", side_effect=_gh_ok) as gh:
        result = change_pr.open_change_pr(_request(repo, _WRITE), gh_token="tok")

    assert (result.outcome, result.detail) == (ChangeOutcome.OPENED, _PR_URL)
    args = gh.call_args.args[0]
    assert args[args.index("--base") + 1] == "master"
    assert args[args.index("--head") + 1] == "chore/hooks"
    assert gh.call_args.kwargs["gh_token"] == "tok"

    _git(repo, "fetch", "-q", "origin")
    assert _git(repo, "show", "origin/chore/hooks:lefthook.yml") == "new"
    assert _git(repo, "rev-parse", "origin/chore/hooks~1") == _git(
        repo, "rev-parse", "origin/master"
    )


def test_the_checkout_is_left_exactly_as_it_was(repo: Path):
    head = _git(repo, "rev-parse", "HEAD")
    with mock.patch.object(change_pr, "run_gh", side_effect=_gh_ok):
        change_pr.open_change_pr(_request(repo, _WRITE))

    assert _git(repo, "branch", "--show-current") == "feature"
    assert _git(repo, "rev-parse", "HEAD") == head
    assert (repo / "lefthook.yml").read_text() == "someone's uncommitted edit\n"
    # Only the checkout itself: the throwaway worktree was removed.
    assert len(_git(repo, "worktree", "list").splitlines()) == 1


def test_a_command_that_changes_nothing_leaves_nothing(repo: Path):
    with mock.patch.object(change_pr, "run_gh", side_effect=_gh_ok) as gh:
        result = change_pr.open_change_pr(_request(repo, ["true"]))

    assert result.outcome is ChangeOutcome.NO_CHANGE
    gh.assert_not_called()
    assert _git(repo, "branch", "--list", "chore/hooks") == ""
    assert _git(repo, "ls-remote", "--heads", "origin", "chore/hooks") == ""
    assert len(_git(repo, "worktree", "list").splitlines()) == 1


def test_a_failing_command_keeps_the_worktree_and_pushes_nothing(repo: Path):
    with (
        mock.patch.object(change_pr, "run_gh", side_effect=_gh_ok) as gh,
        pytest.raises(ChangePrError) as raised,
    ):
        change_pr.open_change_pr(_request(repo, ["false"]))

    gh.assert_not_called()
    assert _git(repo, "ls-remote", "--heads", "origin", "chore/hooks") == ""
    assert raised.value.worktree is not None
    assert raised.value.worktree.is_dir()


def test_a_branch_that_already_exists_is_refused_before_anything_is_made(repo: Path):
    _git(repo, "branch", "chore/hooks")
    with pytest.raises(ChangePrError):
        change_pr.open_change_pr(_request(repo, _WRITE))
    assert len(_git(repo, "worktree", "list").splitlines()) == 1


def test_a_branch_already_on_the_remote_is_refused(repo: Path):
    _git(repo, "push", "-q", "origin", "origin/master:refs/heads/chore/hooks")
    with pytest.raises(ChangePrError):
        change_pr.open_change_pr(_request(repo, _WRITE))


def test_a_missing_repo_is_a_change_error_not_a_crash(tmp_path: Path):
    """Raised as anything else, it escapes the CLI's per-repo handler and every
    repo after it is never tried. tinkercg's directory is gone; it found this."""
    with pytest.raises(ChangePrError):
        change_pr.open_change_pr(_request(tmp_path / "gone", _WRITE))


def test_a_gh_failure_is_raised_with_the_worktree_named(repo: Path):
    failed = subprocess.CompletedProcess([], 1, stdout="", stderr="no permission")
    with (
        mock.patch.object(change_pr, "run_gh", return_value=failed),
        pytest.raises(ChangePrError) as raised,
    ):
        change_pr.open_change_pr(_request(repo, _WRITE))

    assert raised.value.worktree is not None
    # The commit was pushed before gh ran; the branch is there to open the PR by hand.
    assert _git(repo, "ls-remote", "--heads", "origin", "chore/hooks") != ""


# --------------------------------------------------------------------------- #
# `loregarden git change-pr`
# --------------------------------------------------------------------------- #


def _cli(*argv: str) -> int:
    return cli_main.main(["git", "change-pr", "--branch", "b", "--title", "t", *argv])


@pytest.mark.parametrize(
    "argv",
    [
        ["--repo", "/r"],  # no command
        ["--", "true"],  # neither --repo nor --all-workspaces
        ["--repo", "/r", "--all-workspaces", "--", "true"],
    ],
)
def test_cli_usage_errors(argv: list[str]):
    with mock.patch.object(git_tools, "open_change_pr") as opened:
        assert _cli(*argv) == EXIT_USAGE
    opened.assert_not_called()


def test_cli_passes_the_command_after_the_separator(tmp_path: Path):
    result = change_pr.ChangeResult(tmp_path, ChangeOutcome.OPENED, _PR_URL)
    with mock.patch.object(git_tools, "open_change_pr", return_value=result) as opened:
        code = _cli("--repo", str(tmp_path), "--", "bash", "x.sh", "{worktree}")

    assert code == EXIT_OK
    request = opened.call_args.args[0]
    assert request.command == ["bash", "x.sh", "{worktree}"]
    assert request.repo == tmp_path.resolve()


def test_cli_keeps_going_after_a_failure_and_exits_non_zero(tmp_path: Path):
    ok = change_pr.ChangeResult(tmp_path / "b", ChangeOutcome.OPENED, _PR_URL)
    with mock.patch.object(
        git_tools, "open_change_pr", side_effect=[ChangePrError("boom"), ok]
    ) as opened:
        code = _cli("--repo", str(tmp_path / "a"), "--repo", str(tmp_path / "b"), "--", "true")

    assert code == EXIT_ERROR
    assert opened.call_count == 2
