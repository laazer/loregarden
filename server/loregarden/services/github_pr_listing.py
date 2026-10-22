"""A repository's pull requests from GitHub, and whether GitHub said that is all of them.

Every PR listing goes through `list_pull_requests`: the dashboard's open PRs,
the CI poller's, and branch triage's every-state listing. It reads every page in
one `gh api graphql --paginate --slurp` process and takes completeness from the
last page's `pageInfo.hasNextPage`. Comparing the row count with a limit cannot
work: a page holding exactly as many rows as were asked for says nothing about
whether more remain. That inference hid PRs from branch triage once the repo
passed 500 PRs, and from the CI poller past 100 open ones.

A listing that could not be read raises `GitHubReadError`. It never comes back
as an empty list, which would read as "no pull requests".
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Generic, TypeVar

from loregarden.models.domain import PrListingState
from loregarden.services.git_subprocess import run_gh
from pydantic import BaseModel, Field, TypeAdapter, ValidationError

#: GitHub's largest page. `--paginate` follows `pageInfo` to the last page, so
#: this sizes the requests, not the listing.
PR_PAGE_SIZE = 100
_QUERY = """
query($owner: String!, $name: String!, $endCursor: String) {
  repository(owner: $owner, name: $name) {
    pullRequests(%(states)sfirst: %(page_size)d, after: $endCursor) {
      totalCount
      pageInfo { hasNextPage endCursor }
      nodes { %(fields)s }
    }
  }
}
"""


class GitHubReadError(RuntimeError):
    """`gh` could not be asked, or answered with something unreadable."""


class PullRequestStates(Enum):
    """Which pull requests a listing asks for; the value is the GraphQL argument."""

    OPEN = "states: OPEN, "
    ALL = ""


NodeT = TypeVar("NodeT", bound=BaseModel)


class _GhPageInfo(BaseModel):
    has_next_page: bool = Field(alias="hasNextPage")


class _GhPullRequestPage(BaseModel, Generic[NodeT]):
    total_count: int = Field(alias="totalCount")
    page_info: _GhPageInfo = Field(alias="pageInfo")
    nodes: list[NodeT]


class _GhRepository(BaseModel, Generic[NodeT]):
    pull_requests: _GhPullRequestPage[NodeT] = Field(alias="pullRequests")


class _GhData(BaseModel, Generic[NodeT]):
    repository: _GhRepository[NodeT] | None


class GhPage(BaseModel, Generic[NodeT]):
    """One page of the listing; a caller parses the slurped array as `list[GhPage[Node]]`."""

    data: _GhData[NodeT]


@dataclass(frozen=True)
class PullRequestListing(Generic[NodeT]):
    """Every PR GitHub returned, and whether GitHub said that is all of them.

    ``state`` is ``COMPLETE`` or ``INCOMPLETE``, from the last page's
    ``hasNextPage``. A listing that could not be read raises instead of being
    ``UNAVAILABLE``.
    """

    state: PrListingState
    pull_requests: list[NodeT]
    #: GitHub's own count, from the first page.
    total_count: int


def read_gh(repo_root: Path, args: list[str], timeout_seconds: float) -> str:
    """`gh`'s stdout, or `GitHubReadError` naming why there is none."""
    try:
        proc = run_gh(args, cwd=repo_root, timeout=timeout_seconds)
    except subprocess.TimeoutExpired as exc:
        raise GitHubReadError(f"GitHub did not answer within {timeout_seconds:.0f}s") from exc
    except OSError as exc:
        raise GitHubReadError(f"could not run gh: {exc}") from exc
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()
        raise GitHubReadError(detail or f"gh {args[0]} exited {proc.returncode}")
    return proc.stdout or ""


def list_pull_requests(
    repo_root: Path,
    fields: str,
    pages_of: TypeAdapter[list[GhPage[NodeT]]],
    *,
    states: PullRequestStates,
    timeout_seconds: float,
) -> PullRequestListing[NodeT]:
    """Every PR in ``states`` in the repository at ``repo_root``, parsed by ``pages_of``.

    One `gh` process: `--paginate` follows `pageInfo` itself and `--slurp`
    returns the pages as one array, so the last page's `hasNextPage` is still
    there to read. A process per page re-authenticates each time and took
    9-13s for seven pages. ``fields`` is the GraphQL selection each node holds.
    """
    query = _QUERY % {"states": states.value, "page_size": PR_PAGE_SIZE, "fields": fields}
    stdout = read_gh(
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
        timeout_seconds,
    )
    try:
        pages = pages_of.validate_json(stdout)
    except ValidationError as exc:
        raise GitHubReadError(
            f"unreadable GitHub pull request listing: {exc.error_count()} error(s): {exc}"
        ) from exc
    repositories = [page.data.repository for page in pages]
    if not repositories:
        raise GitHubReadError("GitHub returned no pages of pull requests")
    if None in repositories:
        raise GitHubReadError("GitHub has no repository for this checkout's remote")
    listed = [repo.pull_requests for repo in repositories if repo is not None]
    return PullRequestListing(
        state=(
            PrListingState.INCOMPLETE
            if listed[-1].page_info.has_next_page
            else PrListingState.COMPLETE
        ),
        pull_requests=[pr for page in listed for pr in page.nodes],
        total_count=listed[0].total_count,
    )
