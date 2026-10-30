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
from loregarden.services.branch_names import resolve_ticket_branch
from loregarden.services.git_merge_noco import rev
from loregarden.services.git_subprocess import run_gh
from loregarden.services.publish_ref import branch_for_pr_head, publish_ref_for
from loregarden.services.target_branch import (
    TargetBranchError,
    integration_branch_for,
    landing_target,
)
from loregarden.services.workspace_paths import resolve_workspace_root
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator
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


class GhCheck(BaseModel):
    """One `statusCheckRollup` entry: a CheckRun or a legacy StatusContext."""

    model_config = ConfigDict(populate_by_name=True)

    name: str = ""
    context: str = ""
    status: str = ""
    conclusion: str = ""
    state: str = ""
    details_url: str = Field("", alias="detailsUrl")
    target_url: str = Field("", alias="targetUrl")
    #: Whether the base branch's protection requires this check. None when the
    #: read did not ask for it, or asked somewhere GitHub refuses to answer:
    #: `isRequired` needs a pull request number, so a repository-wide listing
    #: cannot carry it. `ci_checks.required_rollup` treats all-None as all-required.
    is_required: bool | None = Field(None, alias="isRequired")

    @field_validator(
        "name",
        "context",
        "status",
        "conclusion",
        "state",
        "details_url",
        "target_url",
        mode="before",
    )
    @classmethod
    def _absent_is_empty(cls, value: str | None) -> str:
        # GitHub's GraphQL API reports an absent value (a running check's
        # conclusion) as null; `gh pr view --json` reports it as "".
        return "" if value is None else value


class GhPullRequest(BaseModel):
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
    checks: list[GhCheck] = Field(default_factory=list, alias="statusCheckRollup")
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
    #: The tree root's shareable id — its `external_id`, or its full UUID when
    #: it has none — so the branch above can be followed to the ticket whose
    #: tab holds the merge. "" when this ticket ships its own branch, or when
    #: no landing target resolved.
    ships_with_root: str = ""
    #: The PR found is the tree's, not this ticket's own: its head is not this
    #: ticket's branch.
    is_tree_pull_request: bool = False


def check_outcome(check: GhCheck) -> PullRequestCheckOutcome:
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


def pull_request_status(raw: GhPullRequest) -> PullRequestStatus:
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
                outcome=check_outcome(check),
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


class ShipsWith(BaseModel):
    """The tree this ticket's work ships in, named at both widths at once.

    Both fields are empty together or set together: a member's root is by
    construction a different, existing ticket, and a landing target that cannot
    be resolved degrades the pair rather than either half of it.
    """

    model_config = ConfigDict(frozen=True)

    branch: str = ""
    root_ref: str = ""


def _ships_with(session: Session, ticket: Ticket, workspace: Workspace) -> ShipsWith:
    try:
        target = landing_target(session, ticket, workspace)
    except TargetBranchError:
        logger.warning("No landing target for %s", ticket.external_id, exc_info=True)
        return ShipsWith()
    if target.root is None:
        return ShipsWith()
    return ShipsWith(
        branch=target.branch, root_ref=target.root.external_id.strip() or target.root.id
    )


def ticket_pull_request(
    session: Session, ticket: Ticket, workspace: Workspace
) -> TicketPullRequest:
    """Ask GitHub for the pull request on ``ticket``'s branch."""
    ships = _ships_with(session, ticket, workspace)
    ships_with = ships.branch
    result = _lookup(session, ticket, workspace, ships_with)
    pr = result.pull_request
    result.ships_with = ships_with
    result.ships_with_root = ships.root_ref
    result.is_tree_pull_request = (
        bool(ships_with)
        and pr is not None
        and branch_for_pr_head(pr.head) != resolve_ticket_branch(ticket)
    )
    return result


def _candidate_branches(ticket: Ticket, repo_root: Path, ships_with: str) -> list[str]:
    """Where this ticket's PR would be, most likely first.

    The tree's branch is asked first, because a tree ships from there: a member
    never publishes its own branch, and a root publishes its integration branch
    (`publish_tree`). ``ships_with`` already names that branch for a member, so
    it is read rather than derived a second time; a root (and a top-level
    ticket, which is its own root) gets "" there and its integration branch is
    computed. The ticket's own branch is asked after it, for a root whose tree
    never formed and for a member that hand-opened a PR of its own. Each name is
    asked as ``publish/<branch>`` (`publish_ref`) first, then bare, for a PR
    opened before publishing moved there.
    """
    own = resolve_ticket_branch(ticket)
    if ships_with:
        tree = ships_with
    else:
        try:
            tree = integration_branch_for(ticket)
        except TargetBranchError:
            tree = ""
    asked = [tree, own] if tree and rev(repo_root, f"refs/heads/{tree}") else [own]
    return [name for branch in asked for name in (publish_ref_for(branch), branch)]


def _lookup(
    session: Session, ticket: Ticket, workspace: Workspace, ships_with: str
) -> TicketPullRequest:
    repo_root = resolve_workspace_root(workspace)
    branches = _candidate_branches(ticket, repo_root, ships_with)
    if not (repo_root / ".git").exists():
        return _failed(session, ticket, branches[0], f"{repo_root} is not a git repository")
    for branch in branches:
        result = _view(session, ticket, repo_root, branch)
        if result.lookup is not PullRequestLookup.NONE:
            return result
    # Built only once every candidate has answered NONE: `branch` is the tree's
    # for a member, so the panel's "ships in its tree's PR" copy names a real
    # branch rather than the last name asked.
    return TicketPullRequest(lookup=PullRequestLookup.NONE, branch=ships_with or branches[-1])


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
        raw = GhPullRequest.model_validate(json.loads(proc.stdout or "{}"))
    except (ValueError, ValidationError) as exc:
        return _failed(session, ticket, branch, f"unexpected gh output: {exc}")
    status = pull_request_status(raw)
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
