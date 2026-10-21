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
from typing import Generic, TypeVar

from loregarden.models.domain import CIStatus, PrListingState, PullRequestCheckOutcome
from loregarden.services.git_subprocess import run_gh
from loregarden.services.publish_ref import branch_for_pr_head
from loregarden.services.ticket_pull_request import GhCheck, check_outcome
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError

GH_TIMEOUT_SECONDS = 30
#: Enough of a failing job's log for an agent to find the error, and small
#: enough to sit in a prompt. The tail is kept, because that is where it fails.
LOG_TAIL_CHARS = 20_000
#: GitHub's largest page. `gh api graphql --paginate` follows `pageInfo` to the
#: last page, so this sizes the requests, not the listing.
PR_PAGE_SIZE = 100
#: The head commit's checks, the `statusCheckRollup` `gh pr list` flattens.
HEAD_CHECKS_FIELDS = """
commits(last: 1) { nodes { commit { statusCheckRollup { contexts(first: 100) { nodes {
  ... on CheckRun { name status conclusion detailsUrl }
  ... on StatusContext { context state targetUrl }
} } } } } }
"""
_OPEN_PRS_QUERY = """
query($owner: String!, $name: String!, $endCursor: String) {
  repository(owner: $owner, name: $name) {
    pullRequests(states: OPEN, first: %(page_size)d, after: $endCursor) {
      pageInfo { hasNextPage endCursor }
      nodes { %(fields)s }
    }
  }
}
"""
_OPEN_FIELDS = "number url headRefName headRefOid " + HEAD_CHECKS_FIELDS
#: A GitHub Actions check run's details URL names its run and its job.
_ACTIONS_JOB_URL = re.compile(r"/actions/runs/\d+/job/(\d+)")


class GitHubReadError(RuntimeError):
    """`gh` could not be asked, or answered with something unreadable."""


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


NodeT = TypeVar("NodeT", bound=BaseModel)


class _GhPageInfo(BaseModel):
    has_next_page: bool = Field(alias="hasNextPage")


class _GhPullRequestPage(BaseModel, Generic[NodeT]):
    page_info: _GhPageInfo = Field(alias="pageInfo")
    nodes: list[NodeT]


class _GhRepository(BaseModel, Generic[NodeT]):
    pull_requests: _GhPullRequestPage[NodeT] = Field(alias="pullRequests")


class _GhData(BaseModel, Generic[NodeT]):
    repository: _GhRepository[NodeT] | None


class GhPage(BaseModel, Generic[NodeT]):
    """One page of `list_open`'s query; a caller parses the slurped array as `list[GhPage[Node]]`."""

    data: _GhData[NodeT]


_OPEN_PAGES = TypeAdapter(list[GhPage[OpenPullRequest]])


@dataclass(frozen=True)
class OpenPullRequests(Generic[NodeT]):
    """Every open PR GitHub returned, and whether GitHub said that is all of them.

    ``state`` is the last page's ``pageInfo.hasNextPage``, never a row count: a
    page holding exactly as many rows as were asked for says nothing about
    whether more remain. It is ``COMPLETE`` or ``INCOMPLETE``; a listing that
    could not be read at all raises `GitHubReadError` instead of being
    ``UNAVAILABLE`` with no rows, which would read as "no open pull requests".
    """

    state: PrListingState
    pull_requests: list[NodeT]


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


def list_open(
    repo_root: Path, fields: str, pages_of: TypeAdapter[list[GhPage[NodeT]]]
) -> OpenPullRequests[NodeT]:
    """Every open PR in the repository at ``repo_root``, each parsed by ``pages_of``.

    One `gh` process: `--paginate` follows `pageInfo` itself and `--slurp`
    returns the pages as one array, so the last page's `hasNextPage` is still
    there to read. ``fields`` is the GraphQL selection each node holds.
    """
    query = _OPEN_PRS_QUERY % {"page_size": PR_PAGE_SIZE, "fields": fields}
    stdout = _gh(
        repo_root,
        [
            "api",
            "graphql",
            "--paginate",
            "--slurp",
            "-F",
            "owner={owner}",
            "-F",
            "name={repo}",
            "-f",
            f"query={query}",
        ],
    )
    try:
        pages = pages_of.validate_json(stdout)
    except ValidationError as exc:
        raise GitHubReadError(f"unexpected GitHub pull request listing: {exc}") from exc
    repositories = [page.data.repository for page in pages]
    if not repositories:
        raise GitHubReadError("GitHub returned no pages of pull requests")
    if None in repositories:
        raise GitHubReadError("GitHub has no repository for this checkout's remote")
    listed = [repo.pull_requests for repo in repositories if repo is not None]
    return OpenPullRequests(
        state=(
            PrListingState.INCOMPLETE
            if listed[-1].page_info.has_next_page
            else PrListingState.COMPLETE
        ),
        pull_requests=[pr for page in listed for pr in page.nodes],
    )


def list_open_pull_requests(repo_root: Path) -> OpenPullRequests[OpenPullRequest]:
    """Every open pull request in the repository at ``repo_root``, with its checks."""
    return list_open(repo_root, _OPEN_FIELDS, _OPEN_PAGES)


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
