"""A workspace's open pull requests, in the order a person should review them.

The dashboard's answer to "where do PRs stand, and in what order do I review
them?". One `gh pr list` call returns every open PR with its checks, review
decision, mergeability and changed files. This module then adds what GitHub
cannot know: which tickets each PR carries, which other open PRs touch the same
files, and a review order.

**The order** is a tier (`PullRequestReviewTier`: ready to merge, to review,
needs work, conflicting, draft), then the fewest overlapping PRs, then the
smallest diff, then the oldest number. Overlap comes before size because
merging one PR changes every PR that shares files with it. Reviewing the
independent ones first means fewer of them go stale, and among PRs that
overlap, the smaller one lands first and the larger one rebases onto it.

Every read here returns what GitHub said or raises `GitHubReadError`, as
`ci_checks` does. "No open pull requests" and "gh could not be asked" must stay
distinguishable, because the second one is how this page first failed: `gh`
answered as the wrong account.
"""

from __future__ import annotations

import re
import subprocess
from datetime import datetime
from itertools import combinations
from pathlib import Path

from loregarden.models.domain import (
    CIStatus,
    PullRequestReview,
    PullRequestReviewTier,
    Ticket,
    TicketState,
    Workspace,
)
from loregarden.services.ci_checks import GitHubReadError, rollup
from loregarden.services.git_branch import resolve_ticket_branch
from loregarden.services.git_subprocess import run_gh
from loregarden.services.publish_ref import branch_for_pr_head
from loregarden.services.ticket_pull_request import (
    GhPullRequest,
    PullRequestStatus,
    pull_request_status,
)
from loregarden.services.workspace_paths import resolve_workspace_root
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError
from sqlmodel import Session, select

GH_TIMEOUT_SECONDS = 30
#: `gh pr list` pages; past this the response says it was cut short.
PR_LIST_LIMIT = 100
_LIST_FIELDS = (
    "number,url,title,state,isDraft,baseRefName,headRefName,additions,deletions,"
    "changedFiles,reviewDecision,mergeable,statusCheckRollup,headRefOid,"
    "mergeStateStatus,body,files,createdAt,updatedAt,author"
)
_TIER_RANK = {tier: index for index, tier in enumerate(PullRequestReviewTier)}


class _GhFile(BaseModel):
    path: str


class _GhAuthor(BaseModel):
    login: str = ""


class _GhListedPullRequest(GhPullRequest):
    model_config = ConfigDict(populate_by_name=True)

    files: list[_GhFile] = Field(default_factory=list)
    created_at: datetime = Field(alias="createdAt")
    updated_at: datetime = Field(alias="updatedAt")
    author: _GhAuthor = Field(default_factory=_GhAuthor)


_LISTED = TypeAdapter(list[_GhListedPullRequest])


class LinkedTicket(BaseModel):
    id: str
    external_id: str
    title: str
    state: TicketState


class PullRequestOverlap(BaseModel):
    number: int
    shared_files: int


class WorkspacePullRequest(BaseModel):
    pull_request: PullRequestStatus
    ci: CIStatus
    author: str
    created_at: datetime
    updated_at: datetime
    tier: PullRequestReviewTier
    #: 1-based position in the review order. Drafts are ranked last, in their own tier.
    review_rank: int
    #: True when GitHub returned fewer files than the PR changes, so overlap is a lower bound.
    files_truncated: bool
    tickets: list[LinkedTicket]
    overlaps: list[PullRequestOverlap]


class WorkspacePullRequests(BaseModel):
    workspace_slug: str
    #: Every open PR, in review order.
    pull_requests: list[WorkspacePullRequest]
    #: True when the listing hit `PR_LIST_LIMIT`; there may be more open PRs.
    truncated: bool


def _list_open(repo_root: Path) -> list[_GhListedPullRequest]:
    args = ["pr", "list", "--state", "open", "--limit", str(PR_LIST_LIMIT), "--json", _LIST_FIELDS]
    try:
        proc = run_gh(args, cwd=repo_root, timeout=GH_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired as exc:
        raise GitHubReadError(f"gh did not answer within {GH_TIMEOUT_SECONDS}s") from exc
    except OSError as exc:
        raise GitHubReadError(f"could not run gh: {exc}") from exc
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()
        raise GitHubReadError(detail or f"gh pr list exited {proc.returncode}")
    try:
        return _LISTED.validate_json(proc.stdout or "[]")
    except ValidationError as exc:
        raise GitHubReadError(f"unexpected gh pr list output: {exc}") from exc


def tier_for(status: PullRequestStatus, ci: CIStatus) -> PullRequestReviewTier:
    if status.is_draft:
        return PullRequestReviewTier.DRAFT
    if status.has_conflicts:
        return PullRequestReviewTier.CONFLICTING
    if ci is CIStatus.FAILING or status.review is PullRequestReview.CHANGES_REQUESTED:
        return PullRequestReviewTier.NEEDS_WORK
    if status.review is PullRequestReview.APPROVED:
        return PullRequestReviewTier.READY_TO_MERGE
    return PullRequestReviewTier.TO_REVIEW


def _overlaps(listed: list[_GhListedPullRequest]) -> dict[int, list[PullRequestOverlap]]:
    files = {pr.number: {f.path for f in pr.files} for pr in listed}
    found: dict[int, list[PullRequestOverlap]] = {pr.number: [] for pr in listed}
    for a, b in combinations(files, 2):
        shared = len(files[a] & files[b])
        if shared:
            found[a].append(PullRequestOverlap(number=b, shared_files=shared))
            found[b].append(PullRequestOverlap(number=a, shared_files=shared))
    for overlaps in found.values():
        overlaps.sort(key=lambda o: (-o.shared_files, o.number))
    return found


class _TicketIndex:
    """Finds the tickets a PR carries: by its head branch, then by ids it names.

    Branch match is the strong signal, because a ticket's own branch is where
    the orchestrator pushes. PRs opened by hand or by an agent session sit on
    arbitrary branches (every open blobert PR does), so an external id named in
    the head branch, title or body counts as well. That is how an integration
    branch (`integration/<root id>`) and a PR whose body says "clears
    blob-core-absorption-150" find their tickets.
    """

    def __init__(self, tickets: list[Ticket]) -> None:
        self._by_branch: dict[str, list[Ticket]] = {}
        self._by_external: dict[str, Ticket] = {}
        for ticket in tickets:
            self._by_branch.setdefault(resolve_ticket_branch(ticket), []).append(ticket)
            if ticket.external_id:
                self._by_external[ticket.external_id] = ticket
        # Longest first, so `x-150` is not read as `x-15` followed by a zero.
        ids = sorted(self._by_external, key=len, reverse=True)
        self._mention = (
            re.compile(r"(?<![\w-])(" + "|".join(map(re.escape, ids)) + r")(?![\w-])")
            if ids
            else None
        )

    def linked(self, pr: _GhListedPullRequest) -> list[LinkedTicket]:
        found: dict[str, Ticket] = {
            t.id: t for t in self._by_branch.get(branch_for_pr_head(pr.head), [])
        }
        if self._mention is not None:
            for match in self._mention.finditer(f"{pr.head}\n{pr.title}\n{pr.body}"):
                ticket = self._by_external[match.group(1)]
                found.setdefault(ticket.id, ticket)
        return [
            LinkedTicket(id=t.id, external_id=t.external_id, title=t.title, state=t.state)
            for t in found.values()
        ]


def _ranked(rows: list[WorkspacePullRequest]) -> list[WorkspacePullRequest]:
    rows.sort(
        key=lambda row: (
            _TIER_RANK[row.tier],
            len(row.overlaps),
            row.pull_request.additions + row.pull_request.deletions,
            row.pull_request.number,
        )
    )
    for index, row in enumerate(rows, start=1):
        row.review_rank = index
    return rows


def workspace_pull_requests(session: Session, workspace: Workspace) -> WorkspacePullRequests:
    """Every open PR in ``workspace``'s repository, in review order."""
    listed = _list_open(resolve_workspace_root(workspace))
    index = _TicketIndex(
        list(session.exec(select(Ticket).where(Ticket.workspace_id == workspace.id)).all())
    )
    overlaps = _overlaps(listed)
    rows: list[WorkspacePullRequest] = []
    for pr in listed:
        status = pull_request_status(pr)
        # The body is on the PR page; carrying 100 of them would dwarf the rest.
        status.body = ""
        ci = rollup(pr.checks)
        rows.append(
            WorkspacePullRequest(
                pull_request=status,
                ci=ci,
                author=pr.author.login,
                created_at=pr.created_at,
                updated_at=pr.updated_at,
                tier=tier_for(status, ci),
                review_rank=0,
                files_truncated=pr.changed_files > len(pr.files),
                tickets=index.linked(pr),
                overlaps=overlaps[pr.number],
            )
        )
    return WorkspacePullRequests(
        workspace_slug=workspace.slug,
        pull_requests=_ranked(rows),
        truncated=len(listed) >= PR_LIST_LIMIT,
    )
