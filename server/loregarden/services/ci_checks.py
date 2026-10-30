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
from dataclasses import dataclass, field
from pathlib import Path

from loregarden.models.domain import CIStatus, PullRequestCheckOutcome
from loregarden.services.github_pr_listing import (
    GhPage,
    GitHubReadError,
    PullRequestListing,
    PullRequestStates,
    list_pull_requests,
    read_gh,
)
from loregarden.services.publish_ref import branch_for_pr_head
from loregarden.services.ticket_pull_request import GhCheck, check_outcome
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

GH_TIMEOUT_SECONDS = 30
#: Enough of a failing job's log for an agent to find the error, and small
#: enough to sit in a prompt. The tail is kept, because that is where it fails.
LOG_TAIL_CHARS = 20_000
#: The head commit's checks, the `statusCheckRollup` `gh pr list` flattens.
HEAD_CHECKS_FIELDS = """
commits(last: 1) { nodes { commit { statusCheckRollup { contexts(first: 100) { nodes {
  ... on CheckRun { name status conclusion detailsUrl }
  ... on StatusContext { context state targetUrl }
} } } } } }
"""
_OPEN_FIELDS = "number url headRefName headRefOid " + HEAD_CHECKS_FIELDS
#: A GitHub Actions check run's details URL names its run and its job.
_ACTIONS_JOB_URL = re.compile(r"/actions/runs/\d+/job/(\d+)")


class _GhCheckNodes(BaseModel):
    nodes: list[GhCheck]


class _GhRollup(BaseModel):
    contexts: _GhCheckNodes


class _GhCommit(BaseModel):
    #: Null when nothing has reported on the commit yet. Required, so a query
    #: that stops asking for it fails to parse instead of reading as no checks.
    status_check_rollup: _GhRollup | None = Field(alias="statusCheckRollup")


class _GhCommitNode(BaseModel):
    commit: _GhCommit


class GhHeadCommit(BaseModel):
    """`commits(last: 1)` from `HEAD_CHECKS_FIELDS`: the head commit and its checks."""

    nodes: list[_GhCommitNode]

    def checks(self) -> list[GhCheck]:
        if not self.nodes or self.nodes[0].commit.status_check_rollup is None:
            return []
        return self.nodes[0].commit.status_check_rollup.contexts.nodes


class OpenPullRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    number: int
    url: str
    head: str = Field(alias="headRefName")
    head_sha: str = Field(alias="headRefOid")
    commits: GhHeadCommit

    @property
    def checks(self) -> list[GhCheck]:
        return self.commits.checks()

    @property
    def branch(self) -> str:
        """The local branch this PR publishes; ``head`` is its name on the remote."""
        return branch_for_pr_head(self.head)


_OPEN_PAGES = TypeAdapter(list[GhPage[OpenPullRequest]])


@dataclass(frozen=True)
class FailureLogs:
    """The failing jobs' log tails, and each check whose log could not be read."""

    text: str
    errors: list[str] = field(default_factory=list)


def list_open_pull_requests(repo_root: Path) -> PullRequestListing[OpenPullRequest]:
    """Every open pull request in the repository at ``repo_root``, with its checks."""
    return list_pull_requests(
        repo_root,
        _OPEN_FIELDS,
        _OPEN_PAGES,
        states=PullRequestStates.OPEN,
        timeout_seconds=GH_TIMEOUT_SECONDS,
    )


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


def required_checks(checks: list[GhCheck]) -> list[GhCheck]:
    """The checks the base branch's protection requires, as far as the read knows.

    `isRequired` needs a pull request number, so a repository-wide listing
    cannot ask for it and every check comes back None. All-None therefore means
    "nobody said", and the conservative reading of that is "all of them": it
    over-reports a failing optional check rather than hiding a failing required
    one. All-False is different — that is GitHub saying none is required.
    """
    # lg-verdict-outcome-1637: on the listing path this is always empty, because
    # `isRequired` cannot be asked for there. The fallback below is why that is
    # safe rather than silent.
    flagged = [check for check in checks if check.is_required is True]
    if flagged:
        return flagged
    if checks and all(check.is_required is None for check in checks):
        return checks
    return []


def required_rollup(checks: list[GhCheck]) -> CIStatus:
    """`rollup` over the required checks only.

    No checks at all is PENDING, as in `rollup`: a commit nothing has judged
    yet has not passed. Checks that exist but none of which is required is
    PASSING, not the empty PENDING: no required check is failing or pending.
    """
    if not checks:
        return rollup(checks)
    required = required_checks(checks)
    if not required:
        return CIStatus.PASSING
    return rollup(required)


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
            log = read_gh(
                repo_root,
                ["run", "view", "--job", match.group(1), "--log-failed"],
                GH_TIMEOUT_SECONDS,
            )
        except GitHubReadError as exc:
            errors.append(f"{name}: could not read its log: {exc}")
            continue
        chunks.append(f"### {name}\n\n{log.strip()}")
    return FailureLogs(text="\n\n".join(chunks)[-LOG_TAIL_CHARS:], errors=errors)
