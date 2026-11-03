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
    Ticket,
    Workspace,
)
from loregarden.services import merge_queue
from loregarden.services.git_automation_config import base_branch
from loregarden.services.merge_queue import STATE_STEP, STEPS, Admission, Candidate, QueueRefusal
from loregarden.services.merge_queue_github import (
    CheckReading,
    GhQueueGitHub,
    QueueGitHub,
    read_required,
)
from loregarden.services.publish_ref import publish_ref_for
from loregarden.services.publish_tree import published_branch
from loregarden.services.target_branch import subtree_root
from loregarden.services.workspace_paths import resolve_workspace_root
from pydantic import BaseModel, TypeAdapter
from sqlmodel import Session

_READINGS = TypeAdapter(list[CheckReading])


class EntryView(BaseModel):
    """One entry as the board and the tools show it."""

    id: str
    ticket_id: str
    ticket_external_id: str
    ticket_title: str
    position: int
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


def _can_retry(entry: MergeQueueEntry) -> bool:
    return entry.state in (
        MergeQueueState.FAILED,
        MergeQueueState.TIMED_OUT,
        MergeQueueState.WITHDRAWN,
    )


def _fields(session: Session, entry: MergeQueueEntry, head_id: str | None) -> dict:
    ticket = session.get(Ticket, entry.ticket_id)
    return {
        "id": entry.id,
        "ticket_id": entry.ticket_id,
        "ticket_external_id": ticket.external_id if ticket else "",
        "ticket_title": ticket.title if ticket else "",
        "position": entry.position,
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
        "can_retry": _can_retry(entry),
        "enqueued_at": entry.enqueued_at,
        "state_changed_at": entry.state_changed_at,
    }


def _head_id(session: Session, workspace_id: str) -> str | None:
    head = merge_queue.head_entry(session, workspace_id)
    return head.id if head else None


def queue_view(session: Session, workspace: Workspace, *, limit: int = 50) -> QueueView:
    """The live entries in order, then the most recent finished ones."""
    head_id = _head_id(session, workspace.id)
    return QueueView(
        workspace_slug=workspace.slug,
        entries=[
            EntryView(**_fields(session, entry, head_id))
            for entry in merge_queue.entries_for(session, workspace.id, limit=limit)
        ],
        head_id=head_id,
    )


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
        **_fields(session, entry, head_id),
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
