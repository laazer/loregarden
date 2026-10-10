"""A ticket's pull request, as GitHub reports it now.

The PR tab used to read only the `pr` artifact, which exists when Loregarden's
own "Open PR" button made the PR. A PR opened any other way (by hand, by an
agent with `gh`, by `publish_tree`) never wrote one, so the tab said "No pull
request opened" beside an open PR with a failing check. GitHub is the source of
truth for whether a branch has a PR, so this asks it, and keeps "there is none"
apart from "the lookup failed".
"""

from __future__ import annotations

import json
import logging
import subprocess
from pathlib import Path

from loregarden.models.domain import (
    Artifact,
    ArtifactKind,
    PullRequestCheckOutcome,
    PullRequestLookup,
    PullRequestReview,
    PullRequestState,
    Ticket,
    Workspace,
)
from loregarden.services.git_branch import resolve_ticket_branch
from loregarden.services.git_merge_noco import rev
from loregarden.services.git_subprocess import run_gh
from loregarden.services.orchestration_profile import resolve_orchestration_profile
from loregarden.services.target_branch import (
    TargetBranchError,
    integration_branch_for,
    subtree_root,
    target_branch_name,
)
from loregarden.services.workspace_paths import resolve_workspace_root
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlmodel import Session, col, select

logger = logging.getLogger(__name__)

GH_TIMEOUT_SECONDS = 15
_GH_FIELDS = (
    "number,url,title,state,isDraft,baseRefName,headRefName,additions,deletions,"
    "changedFiles,reviewDecision,mergeable,statusCheckRollup,body,headRefOid,mergeStateStatus"
)
#: What `gh pr view` prints, on exit 1, when the branch simply has no PR.
_NO_PR_MARKER = "no pull requests found"

# GitHub's vocabularies, which we do not own.
_PASSING_CONCLUSIONS = frozenset({"SUCCESS"})  # py-org: allow-string
_SKIPPED_CONCLUSIONS = frozenset({"SKIPPED", "NEUTRAL"})  # py-org: allow-string
_PENDING_STATES = frozenset(
    {"PENDING", "EXPECTED", "QUEUED", "IN_PROGRESS", "WAITING", "REQUESTED"}
)  # py-org: allow-string
_REVIEW = {
    "APPROVED": PullRequestReview.APPROVED,  # py-org: allow-string
    "CHANGES_REQUESTED": PullRequestReview.CHANGES_REQUESTED,  # py-org: allow-string
    "REVIEW_REQUIRED": PullRequestReview.REVIEW_REQUIRED,  # py-org: allow-string
}
_STATE = {
    "OPEN": PullRequestState.OPEN,  # py-org: allow-string
    "MERGED": PullRequestState.MERGED,  # py-org: allow-string
    "CLOSED": PullRequestState.CLOSED,  # py-org: allow-string
}


class _GhCheck(BaseModel):
    """One `statusCheckRollup` entry: a CheckRun or a legacy StatusContext."""

    model_config = ConfigDict(populate_by_name=True)

    name: str = ""
    context: str = ""
    status: str = ""
    conclusion: str = ""
    state: str = ""
    details_url: str = Field("", alias="detailsUrl")
    target_url: str = Field("", alias="targetUrl")


class _GhPullRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    number: int
    url: str
    title: str
    state: str
    is_draft: bool = Field(False, alias="isDraft")
    base: str = Field("", alias="baseRefName")
    head: str = Field("", alias="headRefName")
    additions: int = 0
    deletions: int = 0
    changed_files: int = Field(0, alias="changedFiles")
    review_decision: str = Field("", alias="reviewDecision")
    mergeable: str = ""
    head_sha: str = Field("", alias="headRefOid")
    merge_state: str = Field("", alias="mergeStateStatus")
    checks: list[_GhCheck] = Field(default_factory=list, alias="statusCheckRollup")
    body: str = ""


class PullRequestCheck(BaseModel):
    name: str
    outcome: PullRequestCheckOutcome
    url: str = ""


class PullRequestStatus(BaseModel):
    number: int
    url: str
    title: str
    state: PullRequestState
    is_draft: bool
    base: str
    head: str
    additions: int
    deletions: int
    changed_files: int
    review: PullRequestReview
    has_conflicts: bool
    checks: list[PullRequestCheck]
    body: str
    #: The commit GitHub would merge; a merge is pinned to it.
    head_sha: str = ""
    #: GitHub's own "this can merge now": open, and `mergeStateStatus` CLEAN
    #: (required checks green, signatures and reviews satisfied, no conflicts).
    mergeable_now: bool = False
    #: "<short sha> <subject>" for each commit GitHub cannot verify, asked only
    #: while the PR is blocked: `main` requires signed commits, and an unsigned
    #: one blocks a PR whose checks are all green (#555).
    unsigned_commits: list[str] = Field(default_factory=list)


class RecordedPullRequest(BaseModel):
    """The PR Loregarden's own "Open PR" recorded, shown when GitHub cannot be asked."""

    url: str
    number: str = ""
    title: str = ""


class TicketPullRequest(BaseModel):
    lookup: PullRequestLookup
    branch: str
    pull_request: PullRequestStatus | None = None
    #: Why the lookup failed, in `gh`'s words. Empty unless `lookup` is FAILED.
    error: str = ""
    recorded: RecordedPullRequest | None = None
    #: The integration branch this ticket's work ships on, when it is inside a
    #: tree; "" for a ticket that ships its own branch. A tree member gets no PR
    #: of its own (`github_pr_service._refuse_tree_member`).
    ships_with: str = ""


def _check_outcome(check: _GhCheck) -> PullRequestCheckOutcome:
    # A CheckRun reports `status` then `conclusion`; a StatusContext only `state`.
    verdict = check.conclusion or check.state
    if (
        check.status and check.status != "COMPLETED"
    ) or verdict in _PENDING_STATES:  # py-org: allow-string
        return PullRequestCheckOutcome.PENDING
    if verdict in _PASSING_CONCLUSIONS:
        return PullRequestCheckOutcome.PASSING
    if verdict in _SKIPPED_CONCLUSIONS:
        return PullRequestCheckOutcome.SKIPPED
    if not verdict:
        return PullRequestCheckOutcome.PENDING
    return PullRequestCheckOutcome.FAILING


def _to_status(raw: _GhPullRequest) -> PullRequestStatus:
    return PullRequestStatus(
        number=raw.number,
        url=raw.url,
        title=raw.title,
        state=_STATE.get(raw.state, PullRequestState.OPEN),
        is_draft=raw.is_draft,
        base=raw.base,
        head=raw.head,
        additions=raw.additions,
        deletions=raw.deletions,
        changed_files=raw.changed_files,
        review=_REVIEW.get(raw.review_decision, PullRequestReview.NOT_REQUIRED),
        has_conflicts=raw.mergeable == "CONFLICTING",  # py-org: allow-string
        checks=[
            PullRequestCheck(
                name=check.name or check.context,
                outcome=_check_outcome(check),
                url=check.details_url or check.target_url,
            )
            for check in raw.checks
        ],
        body=raw.body,
        head_sha=raw.head_sha,
        mergeable_now=raw.state == "OPEN" and raw.merge_state == "CLEAN",  # py-org: allow-string
    )


def _recorded(session: Session, ticket: Ticket) -> RecordedPullRequest | None:
    artifact = session.exec(
        select(Artifact)
        .where(Artifact.ticket_id == ticket.id, Artifact.kind == ArtifactKind.PR)
        .order_by(col(Artifact.created_at).desc())
    ).first()
    if artifact is None:
        return None
    try:
        return RecordedPullRequest.model_validate_json(artifact.content_json or "{}")
    except ValidationError:
        logger.warning(
            "PR artifact %s on %s is malformed", artifact.id, ticket.external_id, exc_info=True
        )
        return None


def _failed(session: Session, ticket: Ticket, branch: str, error: str) -> TicketPullRequest:
    logger.warning("PR lookup for %s (%s) failed: %s", ticket.external_id, branch, error)
    return TicketPullRequest(
        lookup=PullRequestLookup.FAILED,
        branch=branch,
        error=error,
        recorded=_recorded(session, ticket),
    )


def _ships_with(session: Session, ticket: Ticket, workspace: Workspace) -> str:
    try:
        target = target_branch_name(session, ticket, workspace)
    except TargetBranchError:
        logger.warning("No landing target for %s", ticket.external_id, exc_info=True)
        return ""
    return "" if target == resolve_orchestration_profile(workspace).git.base_branch else target


def ticket_pull_request(
    session: Session, ticket: Ticket, workspace: Workspace
) -> TicketPullRequest:
    """Ask GitHub for the pull request on ``ticket``'s branch."""
    result = _lookup(session, ticket, workspace)
    result.ships_with = _ships_with(session, ticket, workspace)
    return result


def _candidate_branches(session: Session, ticket: Ticket, repo_root: Path) -> list[str]:
    """Where this ticket's PR would be, most likely first.

    A tree's root publishes its integration branch (`publish_tree`), so that
    is where its PR lives; its own branch is asked as well, for a root whose
    tree never formed. Any other ticket's PR is on its own branch.
    """
    own = resolve_ticket_branch(ticket)
    try:
        is_root = subtree_root(session, ticket).id == ticket.id
    except TargetBranchError:
        return [own]
    if not is_root:
        return [own]
    integration = integration_branch_for(ticket)
    return [integration, own] if rev(repo_root, f"refs/heads/{integration}") else [own]


def _lookup(session: Session, ticket: Ticket, workspace: Workspace) -> TicketPullRequest:
    repo_root = resolve_workspace_root(workspace)
    branches = _candidate_branches(session, ticket, repo_root)
    if not (repo_root / ".git").exists():
        return _failed(session, ticket, branches[0], f"{repo_root} is not a git repository")
    result = TicketPullRequest(lookup=PullRequestLookup.NONE, branch=branches[-1])
    for branch in branches:
        result = _view(session, ticket, repo_root, branch)
        if result.lookup is not PullRequestLookup.NONE:
            return result
    return result


def _view(session: Session, ticket: Ticket, repo_root: Path, branch: str) -> TicketPullRequest:
    try:
        proc = run_gh(
            ["pr", "view", branch, "--json", _GH_FIELDS], cwd=repo_root, timeout=GH_TIMEOUT_SECONDS
        )
    except subprocess.TimeoutExpired:
        return _failed(session, ticket, branch, f"gh did not answer within {GH_TIMEOUT_SECONDS}s")
    except OSError as exc:
        return _failed(session, ticket, branch, f"could not run gh: {exc}")
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()
        if _NO_PR_MARKER in detail:
            return TicketPullRequest(lookup=PullRequestLookup.NONE, branch=branch)
        return _failed(session, ticket, branch, detail or f"gh pr view exited {proc.returncode}")
    try:
        raw = _GhPullRequest.model_validate(json.loads(proc.stdout or "{}"))
    except (ValueError, ValidationError) as exc:
        return _failed(session, ticket, branch, f"unexpected gh output: {exc}")
    status = _to_status(raw)
    if raw.state == "OPEN" and raw.merge_state == "BLOCKED":  # py-org: allow-string
        status.unsigned_commits = _unsigned_commits(repo_root, raw.number)
    return TicketPullRequest(lookup=PullRequestLookup.FOUND, branch=branch, pull_request=status)


_UNSIGNED_JQ = (
    ".[] | select(.commit.verification.verified | not) "
    '| "\\(.sha[0:8]) \\(.commit.message | split("\\n")[0])"'
)


def _unsigned_commits(repo_root: Path, number: int) -> list[str]:
    """The PR's commits GitHub cannot verify. Best-effort: the verdict falls back
    to "GitHub's rules block it" without them, and a failure is logged."""
    try:
        proc = run_gh(
            [
                "api",
                f"repos/{{owner}}/{{repo}}/pulls/{number}/commits?per_page=100",
                "--jq",
                _UNSIGNED_JQ,
            ],
            cwd=repo_root,
            timeout=GH_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.TimeoutExpired):
        logger.warning("Could not list PR #%s's commits", number, exc_info=True)
        return []
    if proc.returncode != 0:
        logger.warning("Could not list PR #%s's commits: %s", number, (proc.stderr or "").strip())
        return []
    return [line for line in proc.stdout.splitlines() if line.strip()]
