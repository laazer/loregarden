"""Land a scripted change in another repository as a pull request.

The repository's own checkout is never touched. Workspaces are checkouts people
and orchestrations are working in — blobert's sat on `main` with a dozen
uncommitted files when its hook block needed refreshing — so editing one in
place either sweeps the change into whatever commits next or strands it beside
unrelated work. Instead:

1. a throwaway worktree on a new branch, cut from the remote's default branch;
2. the change command, run inside it (``{worktree}`` in its argv is the path);
3. commit, push, `gh pr create`;
4. the worktree is removed. The branch stays, on the remote, behind the PR.

A failure after step 1 keeps the worktree and names it, so the half-made change
can be inspected rather than lost. A command that changes nothing is reported
as such and leaves nothing behind.
"""

from __future__ import annotations

import logging
import subprocess
import tempfile
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from loregarden.services.git_subprocess import run_gh, run_git, scrubbed_git_env

logger = logging.getLogger(__name__)

WORKTREE_PLACEHOLDER = "{worktree}"
COMMAND_TIMEOUT_SECONDS = 300


class ChangeOutcome(StrEnum):
    OPENED = "opened"
    NO_CHANGE = "no_change"


@dataclass(frozen=True)
class ChangeRequest:
    repo: Path
    branch: str
    title: str
    body: str
    #: argv; every ``{worktree}`` is replaced with the worktree's path.
    command: list[str]


@dataclass(frozen=True)
class ChangeResult:
    repo: Path
    outcome: ChangeOutcome
    #: The PR url when opened; what the command reported when it changed nothing.
    detail: str


class ChangePrError(RuntimeError):
    """A step failed. The message names the step, git's reason, and any worktree kept."""

    def __init__(self, message: str, *, worktree: Path | None = None) -> None:
        super().__init__(message)
        #: The half-made change, kept for inspection; None when nothing was made.
        self.worktree = worktree


def _git(repo: Path, *args: str) -> str:
    result = run_git(list(args), cwd=repo, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        reason = (result.stderr or result.stdout).strip() or f"exit {result.returncode}"
        raise ChangePrError(f"git {' '.join(args)} failed in {repo}: {reason}")
    return result.stdout.strip()


def default_branch(repo: Path) -> str:
    """The remote's default branch, asked of the remote rather than a local guess.

    `refs/remotes/origin/HEAD` is only set by `clone`; a repo created with
    `init` + `remote add` has none, and assuming `main` opens PRs against the
    wrong base in loremaker, whose default is `master`.
    """
    for line in _git(repo, "ls-remote", "--symref", "origin", "HEAD").splitlines():
        if line.startswith("ref: refs/heads/"):
            return line.removeprefix("ref: refs/heads/").split("\t", 1)[0]
    raise ChangePrError(f"origin of {repo} does not report a default branch")


def _refuse_existing_branch(repo: Path, branch: str) -> None:
    local = run_git(
        ["rev-parse", "--verify", "--quiet", f"refs/heads/{branch}"],
        cwd=repo,
        capture_output=True,
        text=True,
        check=False,
    )
    if local.returncode == 0:
        raise ChangePrError(f"branch {branch} already exists in {repo}; pick another name")
    if _git(repo, "ls-remote", "--heads", "origin", branch):
        raise ChangePrError(f"branch {branch} already exists on {repo}'s origin; pick another name")


def _run_command(command: list[str], worktree: Path) -> str:
    argv = [part.replace(WORKTREE_PLACEHOLDER, str(worktree)) for part in command]
    try:
        completed = subprocess.run(
            argv,
            cwd=worktree,
            env=scrubbed_git_env(),
            capture_output=True,
            text=True,
            timeout=COMMAND_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ChangePrError(f"change command could not run in {worktree}: {exc}") from exc
    output = "\n".join(
        part.strip() for part in (completed.stdout, completed.stderr) if part.strip()
    )
    if completed.returncode != 0:
        raise ChangePrError(
            f"change command exited {completed.returncode} in {worktree}: {output or '(no output)'}"
        )
    return output


def _gh(args: list[str], cwd: Path, gh_token: str | None) -> str:
    try:
        completed = run_gh(args, cwd=cwd, gh_token=gh_token)
    except OSError as exc:
        raise ChangePrError(f"gh could not run: {exc}") from exc
    if completed.returncode != 0:
        reason = (completed.stderr or completed.stdout).strip() or f"exit {completed.returncode}"
        raise ChangePrError(f"gh {' '.join(args[:2])} failed in {cwd}: {reason}")
    return completed.stdout.strip()


def _remove_worktree(repo: Path, worktree: Path) -> None:
    """Best-effort: the change already landed, and a leftover worktree is visible."""
    result = run_git(
        ["worktree", "remove", "--force", str(worktree)],
        cwd=repo,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        logger.warning(
            "could not remove worktree %s from %s: %s", worktree, repo, result.stderr.strip()
        )


def open_change_pr(request: ChangeRequest, *, gh_token: str | None = None) -> ChangeResult:
    repo = request.repo
    if not repo.is_dir():
        # Checked here, not left to git: a missing cwd raises FileNotFoundError
        # out of subprocess, past every per-repo handler.
        raise ChangePrError(f"{repo} does not exist")
    _git(repo, "rev-parse", "--git-dir")  # a clear error for a non-repository
    _refuse_existing_branch(repo, request.branch)
    base = default_branch(repo)
    _git(repo, "fetch", "--quiet", "origin", base)

    worktree = Path(tempfile.mkdtemp(prefix=f"loregarden-change-{repo.name}-")) / "tree"
    _git(repo, "worktree", "add", "--quiet", "-b", request.branch, str(worktree), f"origin/{base}")

    try:
        output = _run_command(request.command, worktree)
        if not _git(worktree, "status", "--porcelain"):
            _remove_worktree(repo, worktree)
            _git(repo, "branch", "-D", request.branch)  # created above, holds nothing
            return ChangeResult(repo, ChangeOutcome.NO_CHANGE, output or "no files changed")

        _git(worktree, "add", "-A")
        _git(worktree, "commit", "--quiet", "-m", request.title, "-m", request.body)
        _git(worktree, "push", "--quiet", "-u", "origin", request.branch)
        url = _gh(
            [
                "pr",
                "create",
                "--base",
                base,
                "--head",
                request.branch,
                "--title",
                request.title,
                "--body",
                request.body,
            ],
            worktree,
            gh_token,
        ).splitlines()[-1]
    except ChangePrError as exc:
        raise ChangePrError(
            f"{exc}\n(worktree kept for inspection: {worktree})", worktree=worktree
        ) from exc

    _remove_worktree(repo, worktree)
    return ChangeResult(repo, ChangeOutcome.OPENED, url)
