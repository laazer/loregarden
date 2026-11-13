"""What an operator or an agent asks of the merge queue (lg-milestone-that-970/971).

The HTTP routes and the MCP tools both call these, so the board and an agent
cannot read or drive the queue two different ways. Every read that has to ask
GitHub raises `GitHubReadError` when it cannot, and the callers turn that into
an error response: "could not ask" never reads as an empty queue or as no
pull request.
"""

from __future__ import annotations

from datetime import datetime

from loregarden.models.domain import (
    MergeQueueAdmission,
    MergeQueueEntry,
    MergeQueueState,
    MergeQueueWait,
    PullRequestState,
    Ticket,
    WorkItemType,
    Workspace,
)
from loregarden.services import merge_queue
from loregarden.services.git_automation_config import base_branch
from loregarden.services.merge_queue import STATE_STEP, STEPS, Admission, Candidate, QueueRefusal
from loregarden.services.merge_queue_github import (
    CheckReading,
    GhQueueGitHub,
    QueueGitHub,
    StagedPullRequest,
    read_required,
)
from loregarden.services.merge_queue_pull_heads import fetch_pull_head
from loregarden.services.publish_ref import PUBLISH_PREFIX, publish_ref_for
from loregarden.services.publish_tree import published_branch
from loregarden.services.target_branch import subtree_root
from loregarden.services.workspace_paths import resolve_workspace_root
from pydantic import BaseModel, TypeAdapter
from sqlmodel import Session, col, select

_READINGS = TypeAdapter(list[CheckReading])


class EntryView(BaseModel):
    """One entry as the board and the tools show it."""

    id: str
    #: None for a pull request admitted by number.
    ticket_id: str | None
    ticket_external_id: str
    ticket_title: str
    #: Lifetime sequence number in the workspace, for ordering; not a place in line.
    position: int
    #: Live entries in line before this one; 0 for the head and a finished one.
    ahead: int
    state: MergeQueueState
    #: Why a live entry is not moving; "" when nothing holds it.
    wait: MergeQueueWait
    is_head: bool
    pr_number: int
    pr_url: str
    source_branch: str
    head_sha: str
    staged_sha: str
    staged_tree: str
    merge_commit: str
    landed_tree: str
    attempts: int
    detail: str
    #: The failing check's log tail, kept on a failed entry.
    log_tail: str
    conflicted_files: list[str]
    artifact_ref: str
    #: Withdraw is refused once GitHub has been asked to merge.
    can_withdraw: bool
    can_retry: bool
    enqueued_at: datetime
    state_changed_at: datetime


class EntryDetail(EntryView):
    """An entry plus the required checks it waits on."""

    checks: list[CheckReading]
    #: True when ``checks`` was read from GitHub just now; False when it is
    #: what the loop recorded on its last cycle.
    checks_live: bool


class QueueView(BaseModel):
    workspace_slug: str
    entries: list[EntryView]
    head_id: str | None


def _can_withdraw(entry: MergeQueueEntry) -> bool:
    if entry.state is MergeQueueState.QUEUED:
        return True
    step = STATE_STEP.get(entry.state)
    return step is not None and STEPS[step].cancellable


def _can_retry(session: Session, entry: MergeQueueEntry) -> bool:
    """Stopped, and not replaced by a later entry for the same work (whose retry refuses)."""
    return entry.state in merge_queue.STOPPED_STATES and not merge_queue.is_superseded(
        session, entry
    )


def _fields(session: Session, entry: MergeQueueEntry, head_id: str | None, ahead: int) -> dict:
    ticket = merge_queue.entry_ticket(session, entry)
    return {
        "id": entry.id,
        "ticket_id": entry.ticket_id,
        "ticket_external_id": ticket.external_id if ticket else "",
        "ticket_title": ticket.title if ticket else "",
        "position": entry.position,
        "ahead": ahead,
        "state": entry.state,
        "wait": entry.wait,
        "is_head": entry.id == head_id,
        "pr_number": entry.pr_number,
        "pr_url": entry.pr_url,
        "source_branch": entry.source_branch,
        "head_sha": entry.head_sha,
        "staged_sha": entry.staged_sha,
        "staged_tree": entry.staged_tree,
        "merge_commit": entry.merge_commit,
        "landed_tree": entry.landed_tree,
        "attempts": entry.attempts,
        "detail": entry.detail,
        "log_tail": entry.log_tail,
        "conflicted_files": entry.conflicted_files(),
        "artifact_ref": entry.artifact_ref,
        "can_withdraw": _can_withdraw(entry),
        "can_retry": _can_retry(session, entry),
        "enqueued_at": entry.enqueued_at,
        "state_changed_at": entry.state_changed_at,
    }


def _head_id(session: Session, workspace_id: str) -> str | None:
    head = merge_queue.head_entry(session, workspace_id)
    return head.id if head else None


def queue_view(session: Session, workspace: Workspace, *, limit: int = 50) -> QueueView:
    """The live entries in order, then the most recent finished ones."""
    head_id = _head_id(session, workspace.id)
    entries = merge_queue.entries_for(session, workspace.id, limit=limit)
    # entries_for lists the live entries first, in line order, so a live
    # entry's index is its count ahead; one query instead of one per entry.
    live_ids = {entry.id for entry in merge_queue.live_entries(session, workspace.id)}
    return QueueView(
        workspace_slug=workspace.slug,
        entries=[
            EntryView(**_fields(session, entry, head_id, index if entry.id in live_ids else 0))
            for index, entry in enumerate(entries)
        ],
        head_id=head_id,
    )


class QueueOverview(BaseModel):
    """Every workspace's queue that has ever held an entry, for the pages that span workspaces."""

    queues: list[QueueView]


class TicketQueueView(BaseModel):
    """The merge queue as one ticket sees it: its tree's most recent entry.

    A tree lands as one pull request, queued under its root, so a child ticket
    reads the same entry as its root does.
    """

    root_ticket_id: str
    root_external_id: str
    #: None when the tree has never been queued (or ``ticket`` is an initiative,
    #: which roots no tree).
    entry: EntryView | None
    #: Live entries in line before ``entry``; 0 for the head and a finished one.
    ahead: int


def queue_overview(session: Session, *, limit: int = 10) -> QueueOverview:
    """Each workspace with any entry, its live entries then its ``limit`` latest finished."""
    workspace_ids = session.exec(select(MergeQueueEntry.workspace_id).distinct()).all()
    workspaces = session.exec(
        select(Workspace).where(col(Workspace.id).in_(list(workspace_ids))).order_by(Workspace.slug)
    ).all()
    return QueueOverview(
        queues=[queue_view(session, workspace, limit=limit) for workspace in workspaces]
    )


def ticket_queue_view(session: Session, ticket: Ticket) -> TicketQueueView:
    """Where ``ticket``'s tree stands in the merge queue, if it was ever queued."""
    if ticket.work_item_type is WorkItemType.INITIATIVE:
        return TicketQueueView(
            root_ticket_id=ticket.id, root_external_id=ticket.external_id, entry=None, ahead=0
        )
    root = subtree_root(session, ticket)
    latest = session.exec(
        select(MergeQueueEntry)
        .where(MergeQueueEntry.ticket_id == root.id)
        .order_by(col(MergeQueueEntry.enqueued_at).desc())
        .limit(1)
    ).first()
    if latest is None:
        return TicketQueueView(
            root_ticket_id=root.id, root_external_id=root.external_id, entry=None, ahead=0
        )
    ahead = merge_queue.ahead_of(session, latest)
    return TicketQueueView(
        root_ticket_id=root.id,
        root_external_id=root.external_id,
        entry=EntryView(**_fields(session, latest, _head_id(session, latest.workspace_id), ahead)),
        ahead=ahead,
    )


def pull_request_queue_entry(
    session: Session, workspace: Workspace, pr_number: int
) -> EntryView | None:
    """The latest entry for PR ``pr_number`` in ``workspace``'s queue, or None if never queued."""
    latest = session.exec(
        select(MergeQueueEntry)
        .where(MergeQueueEntry.workspace_id == workspace.id)
        .where(MergeQueueEntry.pr_number == pr_number)
        .order_by(col(MergeQueueEntry.enqueued_at).desc())
        .limit(1)
    ).first()
    if latest is None:
        return None
    ahead = merge_queue.ahead_of(session, latest)
    return EntryView(**_fields(session, latest, _head_id(session, workspace.id), ahead))


def entry_detail(
    session: Session,
    entry_id: str,
    *,
    github: QueueGitHub | None = None,
    live: bool = True,
) -> EntryDetail:
    """One entry; a testing entry's checks are read from GitHub when ``live``.

    Raises `LookupError` for an unknown id and `GitHubReadError` when the live
    read fails. The recorded checks are not offered in that case: they are a
    cycle old, and passing them off as the answer is what the caller asked
    not to get.
    """
    entry = session.get(MergeQueueEntry, entry_id)
    if entry is None:
        raise LookupError(f"no merge queue entry {entry_id}")
    head_id = _head_id(session, entry.workspace_id)
    checks = _READINGS.validate_python(entry.checks())
    checks_live = False
    if live and entry.state is MergeQueueState.TESTING and entry.pr_number > 0:
        checks = _live_checks(github or _github(session, entry.workspace_id), entry)
        checks_live = True
    return EntryDetail(
        **_fields(session, entry, head_id, merge_queue.ahead_of(session, entry)),
        checks=checks,
        checks_live=checks_live,
    )


def _live_checks(github: QueueGitHub, entry: MergeQueueEntry) -> list[CheckReading]:
    """The entry's required checks on its PR head, as GitHub reports them now."""
    required = github.required_contexts(entry.base_branch)
    return read_required(github.read_pull_request(entry.pr_number).checks, required)


def enqueue_ticket(
    session: Session,
    ticket: Ticket,
    workspace: Workspace,
    *,
    github: QueueGitHub | None = None,
) -> Admission:
    """Queue the published PR of the tree ``ticket`` belongs to.

    The candidate is the tree root's integration branch tip, and the PR is the
    open one on its publish ref. Raises `QueueRefusal` when there is no tree
    work or no open PR (publishing opens it: `loregarden_land_ticket` on the
    root), and `GitHubReadError` when GitHub cannot say whether there is one.
    """
    root = subtree_root(session, ticket)
    repo_root = resolve_workspace_root(workspace)
    branch, tip = published_branch(repo_root, root)
    if not tip:
        raise QueueRefusal(f"{root.external_id} has no branch {branch} to queue")
    head_branch = publish_ref_for(branch)
    github = github or GhQueueGitHub(repo_root)
    found = github.open_pull_request(head_branch)
    if found is None:
        raise QueueRefusal(
            f"{root.external_id} has no open pull request on {head_branch}; publish it first "
            f"(loregarden_land_ticket ticket_id={root.external_id})"
        )
    number, url = found
    return merge_queue.enqueue(
        session,
        root,
        workspace,
        Candidate(
            pr_number=number,
            pr_url=url,
            pr_head_branch=head_branch,
            source_branch=branch,
            head_sha=tip,
            base_branch=base_branch(workspace),
        ),
    )


def enqueue_pull_request(
    session: Session,
    workspace: Workspace,
    pr_number: int,
    *,
    github: QueueGitHub | None = None,
) -> Admission:
    """Queue open PR ``pr_number`` on its own, with no ticket behind it.

    For a branch that is not a tree's, such as an interactive session's. The
    candidate is the PR's head as GitHub reports it, fetched and pinned here
    (`merge_queue_pull_heads`) because it may have been pushed from another
    clone. Raises `QueueRefusal` for a PR that is not open, targets another
    base, or is a tree's publish PR (queue that by its ticket), and
    `GitHubReadError` when GitHub cannot say.
    """
    repo_root = resolve_workspace_root(workspace)
    github = github or GhQueueGitHub(repo_root)
    pr = github.read_pull_request(pr_number)
    base = base_branch(workspace)
    _refuse_unqueueable(pr, base)
    head, failure = fetch_pull_head(repo_root, pr_number)
    if failure:
        raise QueueRefusal(failure)
    if head != pr.head_sha:
        raise QueueRefusal(
            f"PR #{pr_number}'s head moved from {pr.head_sha[:12]} to {head[:12]} while it "
            "was read; enqueue it again"
        )
    return merge_queue.enqueue(
        session,
        None,
        workspace,
        Candidate(
            pr_number=pr_number,
            pr_url=pr.url,
            pr_head_branch=pr.head_branch,
            source_branch=pr.head_branch,
            head_sha=head,
            base_branch=base,
        ),
    )


def _refuse_unqueueable(pr: StagedPullRequest, base: str) -> None:
    """Raise `QueueRefusal` unless ``pr`` is open, targets ``base`` and is no tree's publish PR.

    ``pr.base_branch`` is the base GitHub reports for this PR, compared against the
    workspace's own answer, never used as one.
    """
    if pr.state is not PullRequestState.OPEN:
        raise QueueRefusal(f"PR #{pr.number} is {pr.state.value}, not open")
    if pr.base_branch != base:
        raise QueueRefusal(
            f"PR #{pr.number} targets {pr.base_branch}; the queue lands on {base} only"
        )
    if pr.head_branch.startswith(PUBLISH_PREFIX):
        raise QueueRefusal(
            f"PR #{pr.number} publishes a ticket tree ({pr.head_branch}); queue it by a "
            "ticket in that tree instead"
        )


def admitted_or_refuse(admission: Admission) -> Admission:
    """An admission a caller can report as done; any other outcome is a refusal."""
    if admission.admitted or admission.outcome is MergeQueueAdmission.DUPLICATE_HEAD:
        return admission
    raise QueueRefusal(admission.detail)


def _github(session: Session, workspace_id: str) -> QueueGitHub:
    workspace = session.get(Workspace, workspace_id)
    if workspace is None:
        raise LookupError(f"no workspace {workspace_id}")
    return GhQueueGitHub(resolve_workspace_root(workspace))
