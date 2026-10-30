"""Which done tickets have a ship that is not green, and whether GitHub was asked.

`workflow_state._derive_ticket_state` reads the stage map and nothing else, so
a ticket reaches DONE before any remote check has run. `ci_service` is armed at
exactly that point, and when it refuses — budget spent, an unreachable branch, a
head that moved, no `CIRunResult` ever recorded — nothing re-surfaces the
ticket. A shipped ticket and a ticket whose ship is broken read identically.

This module is the other half of that handoff. It is a read: it asks GitHub
which of a done ticket's *carrying* branches has an open pull request whose
required checks are failing or pending, and whether a branch with no pull
request ever reached the remote at all. It writes a `ShipProbe` per workspace
and nothing else — no ticket state, no CI auto-fix path.

Three decisions worth knowing before changing anything here.

**The carrying branch is the tree root's, not the ticket's own.** A done child
of an in-progress feature ships in the tree's PR (`integration/<root>`,
published as `publish/integration/<root>`). Its own `loregarden/<child>` branch
may have no upstream and no PR, and a red PR on it is not the ship. The
candidates are the root's integration branch and the root's own branch, and
the local integration ref is deliberately not consulted: a reconcile checkout
missing `refs/heads/integration/<root>` would fall back to the root's branch
and miss the very PR this exists to report.

**Empty is never green.** Every read either returns what GitHub said or raises
`GitHubReadError`, and a probe that could not ask records `UNAVAILABLE` while
keeping the previous pass's violations. `violations_for_invariant` then returns
nothing, so the reconcile pass writes no findings and retracts none: a GitHub
outage must not read as "every ship is green".

**A branch that exists nowhere is not a ship to push.** Never-reached-remote
is the recoverable half of "it never shipped": the work is sitting in the
checkout and one `git push` sends it. A carrying branch that is in neither the
remote nor the reconcile checkout is a different, worse state — the work is not
anywhere to be pushed — and this invariant's `unrepaired_action` ("push the
published branch") is a lie about it. Measured read-only on the live database
2026-10-28: all 75 never-reached-remote rows were that state, on seven carrying
branches present in no repository, and they were 75 of the 169 rows the
operator was asked to act on. They are gated out here and tracked by
`lg-verdict-outcome-1647` instead.

**`CIRunResult` is evidence, not the predicate.** The 1624 shape has no
`CIRunResult` on the child at all, which is exactly why the CI loop never
reported it. The root's row is copied into the violation for a reader and
decides nothing.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from loregarden.models.domain import (
    Artifact,
    CIRunResult,
    CIStatus,
    MonitorArtifactKind,
    MonitorCondition,
    PrListingState,
    PullRequestCheckOutcome,
    ShipProbe,
    ShipProbeView,
    ShipReason,
    ShipViolation,
    Ticket,
    TicketState,
    WorkItemType,
    Workspace,
)
from loregarden.models.domain.enums import utcnow
from loregarden.services.active_workspaces import active_workspaces
from loregarden.services.branch_names import resolve_ticket_branch
from loregarden.services.ci_checks import (
    GH_TIMEOUT_SECONDS,
    OpenPullRequest,
    check_name,
    list_open_pull_requests,
    required_checks,
    required_rollup,
)
from loregarden.services.git_subprocess import run_git
from loregarden.services.github_pr_listing import (
    GhPage,
    GitHubReadError,
    PullRequestListing,
    PullRequestStates,
    list_pull_requests,
)
from loregarden.services.invariants import Violation
from loregarden.services.publish_ref import PUBLISH_PREFIX, branch_for_pr_head
from loregarden.services.target_branch import (
    TargetBranchError,
    integration_branch_for,
    subtree_root,
)
from loregarden.services.ticket_pull_request import GhCheck, check_outcome
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter
from sqlmodel import Session, col, desc, select

logger = logging.getLogger(__name__)

#: What `load_probe` says when no pass has run yet. Not "no violations".
NO_PROBE_YET = "Ship probe has not run yet"
#: `git ls-remote` is a network read like the `gh` ones; the same budget.
LS_REMOTE_TIMEOUT_SECONDS = GH_TIMEOUT_SECONDS
_NON_GREEN = (CIStatus.FAILING, CIStatus.PENDING)
#: This invariant's name, and the stage key its findings are titled with.
INVARIANT_NAME = "done_ticket_ship_is_observable"
FINDING_TITLE = f"{MonitorCondition.INVARIANT_UNREPAIRED.value}:{INVARIANT_NAME}"


class ListedPullRequest(BaseModel):
    """A pull request in any state: enough to say "this branch has one"."""

    model_config = ConfigDict(populate_by_name=True)

    number: int
    url: str = ""
    state: str = ""
    head: str = Field("", alias="headRefName")

    @property
    def branch(self) -> str:
        return branch_for_pr_head(self.head)


_LISTED_FIELDS = "number url state headRefName"
_LISTED_PAGES = TypeAdapter(list[GhPage[ListedPullRequest]])


@dataclass(frozen=True)
class WorkspaceProbe:
    """One workspace's pass: what GitHub said, or why it could not be asked."""

    listing_state: PrListingState
    error: str
    violations: list[ShipViolation]


# -- the carrying branches ------------------------------------------------------


def carrying_candidates(session: Session, ticket: Ticket) -> list[str]:
    """The branches that could carry ``ticket``'s work to the remote.

    The tree root's integration branch and the root's own branch, in that
    order, deduplicated. Never the ticket's own branch when it is a tree
    member: its work ships in the tree's PR.
    """
    root = subtree_root(session, ticket)
    candidates: list[str] = []
    for branch in (integration_branch_for(root), resolve_ticket_branch(root)):
        if branch and branch not in candidates:
            candidates.append(branch)
    return candidates


def _done_tickets(session: Session, workspace: Workspace) -> list[Ticket]:
    """Every done ticket in ``workspace``. WONT_DO is not done; it never shipped."""
    return list(
        session.exec(
            select(Ticket).where(
                col(Ticket.workspace_id) == workspace.id,
                col(Ticket.state) == TicketState.DONE,
                col(Ticket.work_item_type) != WorkItemType.INITIATIVE,
            )
        ).all()
    )


# -- what GitHub said -----------------------------------------------------------


def _open_by_branch(listing: PullRequestListing[OpenPullRequest]) -> dict[str, OpenPullRequest]:
    """The newest open PR per local branch it publishes."""
    newest: dict[str, OpenPullRequest] = {}
    for pr in listing.pull_requests:
        held = newest.get(pr.branch)
        if held is None or pr.number > held.number:
            newest[pr.branch] = pr
    return newest


def _any_state_branches(listing: PullRequestListing[ListedPullRequest]) -> set[str]:
    """Every local branch GitHub has a pull request for, in any state."""
    return {branch_for_pr_head(pr.head) for pr in listing.pull_requests}


def _non_green_required_names(checks: list[GhCheck]) -> list[str]:
    """The required checks that are failing or still running, in GitHub's order."""
    return [
        check_name(check)
        for check in required_checks(checks)
        if check_outcome(check)
        in (PullRequestCheckOutcome.FAILING, PullRequestCheckOutcome.PENDING)
    ]


def _root_ci_status(session: Session, root: Ticket) -> CIStatus | None:
    """The tree root's newest recorded CI status, as evidence. Never the child's."""
    row = session.exec(
        select(CIRunResult)
        .where(col(CIRunResult.ticket_id) == root.id)
        .order_by(desc(col(CIRunResult.created_at)))
    ).first()
    return row.status if row is not None else None


def _remote_heads(repo_root: Path) -> frozenset[str]:
    """Every branch name the remote holds, from one `git ls-remote --heads origin`.

    Raises `GitHubReadError` rather than returning an empty set: "the remote
    has no such branch" and "the remote could not be read" decide opposite
    things here, and only the first one is a violation.
    """
    try:
        proc = run_git(
            ["ls-remote", "--heads", "origin"],
            cwd=repo_root,
            capture_output=True,
            text=True,
            check=False,
            timeout=LS_REMOTE_TIMEOUT_SECONDS,
        )
    except OSError as exc:
        raise GitHubReadError(f"could not run git ls-remote: {exc}") from exc
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()
        raise GitHubReadError(detail or f"git ls-remote exited {proc.returncode}")
    heads: set[str] = set()
    for line in (proc.stdout or "").splitlines():
        _, _, ref = line.partition("\t")
        name = ref.strip().removeprefix("refs/heads/")
        if name and name != ref.strip():
            heads.add(name)
    return frozenset(heads)


def _local_heads(repo_root: Path) -> frozenset[str]:
    """Every branch the reconcile checkout holds, from one `git for-each-ref`.

    Raises `GitHubReadError` for the same reason `_remote_heads` does: a
    checkout that could not be read must not be mistaken for one holding no
    branches, which would gate out every never-reached-remote row.
    """
    try:
        proc = run_git(
            ["for-each-ref", "--format=%(refname:short)", "refs/heads"],
            cwd=repo_root,
            capture_output=True,
            text=True,
            check=False,
            timeout=LS_REMOTE_TIMEOUT_SECONDS,
        )
    except OSError as exc:
        raise GitHubReadError(f"could not run git for-each-ref: {exc}") from exc
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()
        raise GitHubReadError(detail or f"git for-each-ref exited {proc.returncode}")
    return frozenset(line.strip() for line in (proc.stdout or "").splitlines() if line.strip())


def _reached_remote(candidates: list[str], heads: frozenset[str]) -> bool:
    return any(branch in heads or f"{PUBLISH_PREFIX}{branch}" in heads for branch in candidates)


# -- the predicate --------------------------------------------------------------


def _red_candidate(
    candidates: list[str], open_by_branch: dict[str, OpenPullRequest]
) -> tuple[str, OpenPullRequest, CIStatus] | None:
    """The carrying candidate whose open PR has a failing or pending required check.

    Every candidate is considered, so a red head is not hidden by the other
    candidate's newer green PR. The newest names the PR when more than one is
    red, the same rank `branch_triage` uses.
    """
    red = []
    for branch in candidates:
        pr = open_by_branch.get(branch)
        if pr is None:
            continue
        status = required_rollup(pr.checks)
        if status in _NON_GREEN:
            red.append((branch, pr, status))
    if not red:
        return None
    return max(red, key=lambda item: item[1].number)


def _violation_for(
    session: Session,
    ticket: Ticket,
    *,
    candidates: list[str],
    open_by_branch: dict[str, OpenPullRequest],
    listed_branches: set[str],
    listing_state: PrListingState,
    heads: frozenset[str],
    local_heads: frozenset[str],
) -> ShipViolation | None:
    root = subtree_root(session, ticket)
    red = _red_candidate(candidates, open_by_branch)
    if red is not None:
        branch, pr, status = red
        return ShipViolation(
            ticket_id=ticket.id,
            external_id=ticket.external_id,
            title=ticket.title,
            reason=ShipReason.FAILING if status is CIStatus.FAILING else ShipReason.PENDING,
            pr_number=pr.number,
            pr_url=pr.url,
            check_names=_non_green_required_names(pr.checks),
            carrying_branch=branch,
            ci_run_result=_root_ci_status(session, root),
        )
    if any(branch in listed_branches for branch in candidates):
        # A pull request exists on a carrying branch and its required checks are
        # green, merged or closed. Either way, the ship is not broken.
        return None
    if listing_state is not PrListingState.COMPLETE:
        # Pages remained unread, so "no pull request on this branch" is unknown
        # rather than true, and never-reached-remote cannot be concluded.
        return None
    if root.state is not TicketState.DONE or root.landed_sha:
        # No pull request anywhere, so there is a ship to miss only once the
        # tree itself is finished: a done child of an open tree is ordinary
        # mid-flight work, because nothing publishes until the root finishes.
        # Measured read-only on the live database 2026-10-28: without this,
        # never-reached-remote is 267 tickets, 192 of them done children of
        # in-progress or blocked roots — the set the acceptance criteria name
        # as explicitly not this list. With it, 75.
        #
        # A root that recorded a landing shipped, whether or not a pull request
        # carried it. The ROOT's `landed_sha` only: a child's is landing into
        # the tree, and is deliberately not consulted anywhere here.
        return None
    if _reached_remote(candidates, heads):
        # Pushed with no pull request yet: out of this invariant's scope.
        return None
    if not any(branch in local_heads for branch in candidates):
        # The carrying branch is in no repository, so there is nothing to push
        # and this invariant's action cannot be carried out. See the module
        # docstring: that state is `lg-verdict-outcome-1647`, not this one.
        return None
    return ShipViolation(
        ticket_id=ticket.id,
        external_id=ticket.external_id,
        title=ticket.title,
        reason=ShipReason.NEVER_REACHED_REMOTE,
        pr_number=None,
        pr_url=None,
        check_names=[],
        carrying_branch=candidates[0] if candidates else "",
        ci_run_result=_root_ci_status(session, root),
    )


def probe_workspace(session: Session, workspace: Workspace) -> WorkspaceProbe:
    """Ask GitHub about one workspace's done tickets. Never raises."""
    repo_root = Path(workspace.repo_path or ".")
    try:
        open_listing = list_open_pull_requests(repo_root)
        all_listing = list_pull_requests(
            repo_root,
            _LISTED_FIELDS,
            _LISTED_PAGES,
            states=PullRequestStates.ALL,
            timeout_seconds=GH_TIMEOUT_SECONDS,
        )
    except GitHubReadError as exc:
        logger.warning("Ship probe could not ask GitHub for %s: %s", workspace.slug, exc)
        return WorkspaceProbe(PrListingState.UNAVAILABLE, str(exc), [])
    listing_state = (
        PrListingState.COMPLETE
        if open_listing.state is PrListingState.COMPLETE
        and all_listing.state is PrListingState.COMPLETE
        else PrListingState.INCOMPLETE
    )
    try:
        # Once per workspace each, and part of the ask rather than a lazy
        # extra: a repository that could not be read makes this pass
        # UNAVAILABLE, because "there is no such branch" is a violation and
        # "the branches could not be read" must never be mistaken for one.
        heads = _remote_heads(repo_root)
        local_heads = _local_heads(repo_root)
    except GitHubReadError as exc:
        logger.warning("Ship probe could not read %s's branches: %s", workspace.slug, exc)
        return WorkspaceProbe(PrListingState.UNAVAILABLE, str(exc), [])
    open_by_branch = _open_by_branch(open_listing)
    listed_branches = _any_state_branches(all_listing)
    violations: list[ShipViolation] = []
    for ticket in _done_tickets(session, workspace):
        try:
            candidates = carrying_candidates(session, ticket)
        except TargetBranchError as exc:
            logger.warning("Ship probe skipped %s: %s", ticket.external_id, exc)
            continue
        hit = _violation_for(
            session,
            ticket,
            candidates=candidates,
            open_by_branch=open_by_branch,
            listed_branches=listed_branches,
            listing_state=listing_state,
            heads=heads,
            local_heads=local_heads,
        )
        if hit is not None:
            violations.append(hit)
    return WorkspaceProbe(listing_state, "", violations)


def detect_broken_ships(session: Session) -> list[ShipViolation]:
    """Every done ticket whose ship is not green, asked live, across workspaces.

    A workspace GitHub could not be asked about contributes nothing and does
    not stop the others; `probe_all_workspaces` is what records that it was
    unavailable rather than green.
    """
    found: list[ShipViolation] = []
    for workspace in active_workspaces(session):
        found.extend(probe_workspace(session, workspace).violations)
    return found


# -- the probe ------------------------------------------------------------------


def _probe_row(session: Session, workspace_id: str) -> ShipProbe | None:
    return session.exec(
        select(ShipProbe).where(col(ShipProbe.workspace_id) == workspace_id)
    ).first()


def record_probe(session: Session, workspace: Workspace, probe: WorkspaceProbe) -> ShipProbe:
    """Replace ``workspace``'s current probe row with this pass's answer.

    UNAVAILABLE keeps the previous row's violations: the last red ship anyone
    saw stays on screen while GitHub is down, labelled as last-known rather
    than silently dropped.
    """
    row = _probe_row(session, workspace.id)
    if row is None:
        row = ShipProbe(workspace_id=workspace.id)
    row.probed_at = utcnow()
    row.listing_state = probe.listing_state
    row.error = probe.error
    if probe.listing_state is not PrListingState.UNAVAILABLE:
        row.set_violations(probe.violations)
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


def probe_all_workspaces(session: Session) -> list[ShipProbe]:
    """One pass: ask GitHub per workspace and record each answer."""
    return [
        record_probe(session, workspace, probe_workspace(session, workspace))
        for workspace in active_workspaces(session)
    ]


def load_probe(session: Session, workspace_id: str) -> ShipProbeView:
    """One workspace's current probe. Reads the row; never calls `gh`."""
    workspace = session.get(Workspace, workspace_id)
    slug = workspace.slug if workspace is not None else ""
    row = _probe_row(session, workspace_id)
    if row is None:
        return ShipProbeView(
            workspace_id=workspace_id,
            workspace_slug=slug,
            probed_at=None,
            listing_state=PrListingState.UNAVAILABLE,
            error=NO_PROBE_YET,
            violations=[],
        )
    return ShipProbeView(
        workspace_id=workspace_id,
        workspace_slug=slug,
        probed_at=row.probed_at,
        listing_state=row.listing_state,
        error=row.error or None,
        violations=row.violations(),
    )


def load_probes(session: Session, *, workspace: str = "") -> list[ShipProbeView]:
    """Every active workspace's current probe, or just one named by slug."""
    workspaces = [ws for ws in active_workspaces(session) if not workspace or ws.slug == workspace]
    return [load_probe(session, ws.id) for ws in workspaces]


def violations_for_invariant(session: Session) -> list[Violation]:
    """The recorded violations the reconcile pass may report, from COMPLETE probes.

    A probe that is UNAVAILABLE or INCOMPLETE contributes nothing, so nothing
    is reported *and* nothing is retracted from an answer GitHub did not give.
    """
    found: list[Violation] = []
    for workspace in active_workspaces(session):
        row = _probe_row(session, workspace.id)
        if row is None or row.listing_state is not PrListingState.COMPLETE:
            continue
        found.extend(
            Violation(
                subject_id=hit.ticket_id,
                ticket_id=hit.ticket_id,
                entered_at=None,
                detail=hit.detail,
            )
            for hit in row.violations()
        )
    return found


def retract_resolved_ships(session: Session) -> int:
    """Drop this invariant's findings for tickets a COMPLETE probe no longer names.

    Only on COMPLETE, and only this invariant's title: an UNAVAILABLE or
    INCOMPLETE pass is not GitHub saying a ship went green, and another
    invariant's finding on the same ticket is not ours to clear.
    """
    dropped = 0
    for workspace in active_workspaces(session):
        row = _probe_row(session, workspace.id)
        if row is None or row.listing_state is not PrListingState.COMPLETE:
            continue
        still_violating = {hit.ticket_id for hit in row.violations()}
        stale = session.exec(
            select(Artifact)
            .join(Ticket, col(Artifact.ticket_id) == col(Ticket.id))
            .where(
                col(Ticket.workspace_id) == workspace.id,
                col(Artifact.kind) == MonitorArtifactKind.FINDING.value,
                col(Artifact.title) == FINDING_TITLE,
            )
        ).all()
        for artifact in stale:
            if artifact.ticket_id in still_violating:
                continue
            session.delete(artifact)
            dropped += 1
    if dropped:
        session.commit()
    return dropped
