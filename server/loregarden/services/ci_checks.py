"""What GitHub reports about a workspace's open pull requests and their checks.

This is the CI loop's input. The webhook could not supply it: GitHub cannot
reach a control plane running on localhost, and `ci_run_results` held no rows
at all when this was written. So the poller asks `gh`, the same way the PR tab
does (`ticket_pull_request`), and it uses that module's reading of a single
check so the tab and the loop cannot disagree about what "failing" means.

Every read here either returns what GitHub said or raises `GitHubReadError`.
None of them return an empty list on failure: "no open pull requests" and "gh
could not be asked" must stay distinguishable to the caller.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from loregarden.models.domain import CIStatus, PullRequestCheckOutcome
from loregarden.services.git_subprocess import run_gh
from loregarden.services.publish_ref import branch_for_pr_head
from loregarden.services.ticket_pull_request import GhCheck, check_outcome
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError

GH_TIMEOUT_SECONDS = 30
#: Enough of a failing job's log for an agent to find the error, and small
#: enough to sit in a prompt. The tail is kept, because that is where it fails.
LOG_TAIL_CHARS = 20_000
#: `gh pr list` pages; a workspace with more open PRs than this is not one we have.
PR_LIST_LIMIT = 100
_LIST_FIELDS = "number,url,headRefName,headRefOid,statusCheckRollup"
#: A GitHub Actions check run's details URL names its run and its job.
_ACTIONS_JOB_URL = re.compile(r"/actions/runs/\d+/job/(\d+)")


class GitHubReadError(RuntimeError):
    """`gh` could not be asked, or answered with something unreadable."""


class OpenPullRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    number: int
    url: str
    head: str = Field(alias="headRefName")
    head_sha: str = Field(alias="headRefOid")
    checks: list[GhCheck] = Field(default_factory=list, alias="statusCheckRollup")

    @property
    def branch(self) -> str:
        """The local branch this PR publishes; ``head`` is its name on the remote."""
        return branch_for_pr_head(self.head)


_PULL_REQUESTS = TypeAdapter(list[OpenPullRequest])


@dataclass(frozen=True)
class FailureLogs:
    """The failing jobs' log tails, and each check whose log could not be read."""

    text: str
    errors: list[str] = field(default_factory=list)


def _gh(repo_root: Path, args: list[str]) -> str:
    try:
        proc = run_gh(args, cwd=repo_root, timeout=GH_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired as exc:
        raise GitHubReadError(f"gh did not answer within {GH_TIMEOUT_SECONDS}s") from exc
    except OSError as exc:
        raise GitHubReadError(f"could not run gh: {exc}") from exc
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()
        raise GitHubReadError(detail or f"gh {args[0]} exited {proc.returncode}")
    return proc.stdout or ""


def list_open_pull_requests(repo_root: Path) -> list[OpenPullRequest]:
    """Every open pull request in the repository at ``repo_root``, with its checks."""
    stdout = _gh(
        repo_root,
        ["pr", "list", "--state", "open", "--limit", str(PR_LIST_LIMIT), "--json", _LIST_FIELDS],
    )
    try:
        return _PULL_REQUESTS.validate_json(stdout or "[]")
    except ValidationError as exc:
        raise GitHubReadError(f"unexpected gh pr list output: {exc}") from exc


def rollup(checks: list[GhCheck]) -> CIStatus:
    """One verdict for a commit's checks. Settled only when nothing is still running.

    No checks at all is PENDING, not PASSING: GitHub reports a commit's checks
    a little after the push, and a commit nothing has judged yet has not passed.
    """
    outcomes = [check_outcome(check) for check in checks]
    if not outcomes or PullRequestCheckOutcome.PENDING in outcomes:
        return CIStatus.PENDING
    if PullRequestCheckOutcome.FAILING in outcomes:
        return CIStatus.FAILING
    if all(outcome is PullRequestCheckOutcome.SKIPPED for outcome in outcomes):
        return CIStatus.SKIPPED
    return CIStatus.PASSING


def failing_checks(checks: list[GhCheck]) -> list[GhCheck]:
    return [check for check in checks if check_outcome(check) is PullRequestCheckOutcome.FAILING]


def check_name(check: GhCheck) -> str:
    return check.name or check.context or "unnamed check"


def failure_logs(repo_root: Path, checks: list[GhCheck]) -> FailureLogs:
    """The failed steps' output of every failing GitHub Actions job in ``checks``.

    A check that is not an Actions job (a third-party status) has no log `gh`
    can read; that, and any read that fails, is named in `errors` rather than
    dropped, so the brief can say which checks it has no log for.
    """
    chunks: list[str] = []
    errors: list[str] = []
    for check in failing_checks(checks):
        name = check_name(check)
        url = check.details_url or check.target_url
        match = _ACTIONS_JOB_URL.search(url)
        if match is None:
            errors.append(f"{name}: not a GitHub Actions job, no log to read ({url or 'no URL'})")
            continue
        try:
            log = _gh(repo_root, ["run", "view", "--job", match.group(1), "--log-failed"])
        except GitHubReadError as exc:
            errors.append(f"{name}: could not read its log: {exc}")
            continue
        chunks.append(f"### {name}\n\n{log.strip()}")
    return FailureLogs(text="\n\n".join(chunks)[-LOG_TAIL_CHARS:], errors=errors)
