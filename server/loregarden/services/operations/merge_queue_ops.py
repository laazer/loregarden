"""The merge queue, as operations: read it, put a PR in it, and withdraw or
retry an entry.

These were written twice, as MCP tools and as routes under `/api/merge-queue`,
and enqueueing a ticket's PR had drifted. The tool found the ticket anywhere and
queued it under the ticket's own workspace, ignoring the slug it was given; the
route queued it under the URL's workspace, whatever workspace the ticket was in.
The same request could resolve, or queue, differently on each. Now a ticket
named with a workspace must belong to it, and a mismatch is refused rather than
queued under the wrong workspace.

The read differs by transport on purpose: an agent gets the live line and the
last `STATUS_HISTORY` outcomes, the board asks for as many as it pages.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from loregarden.models.domain import MergeQueueAction, MergeQueueAdmission, Workspace
from loregarden.services import merge_queue
from loregarden.services.merge_queue import Admission, QueueNotFound
from loregarden.services.merge_queue_requests import (
    EntryDetail,
    EntryView,
    QueueView,
    admitted_or_refuse,
    enqueue_pull_request,
    enqueue_ticket,
    entry_detail,
    queue_view,
)
from loregarden.services.operations.spec import (
    Caller,
    Effect,
    Operation,
    OperationId,
    Transport,
)
from loregarden.services.orchestration_callbacks import OrchestrationCallbackService
from loregarden.services.ticket_workspace_binding import workspace_for_ticket
from pydantic import BaseModel, ConfigDict, Field
from sqlmodel import Session, select

#: MCP callers have always been able to send keys a tool does not read.
_LENIENT = ConfigDict(extra="ignore", use_attribute_docstrings=True)

#: Finished entries an agent's status read lists after the live ones. The queue
#: keeps every entry it ever held; an agent asking what is landing needs the
#: line and its last few outcomes, not the whole history.
STATUS_HISTORY = 10


class QueueRead(BaseModel):
    """One entry by ``entry_id``, or a workspace's queue by ``workspace_slug``."""

    model_config = _LENIENT

    workspace_slug: str = ""
    """Workspace whose queue to read."""
    entry_id: str = ""
    """One entry, with its checks, instead of the queue."""
    #: Read a testing entry's checks from GitHub now, rather than the last cycle's.
    live: bool = True
    """Read a testing entry's checks from GitHub now (default true); false returns what the loop
    recorded on its last cycle.
    """
    #: Finished entries the board asks for; an agent always gets STATUS_HISTORY.
    limit: int = Field(default=50, ge=1, le=500)


class EnqueueArgs(BaseModel):
    """Exactly one of ``ticket_id`` (a tree's PR) or ``pr_number`` (any other PR)."""

    model_config = _LENIENT

    #: A UUID or an external id; any ticket in the tree.
    ticket_id: str = ""
    """Ticket UUID or external id, for a tree's PR."""
    pr_number: int = Field(default=0, ge=0)
    """Pull request number, for a PR with no ticket."""
    #: Required with ``pr_number``; with ``ticket_id``, the ticket must belong to it.
    workspace_slug: str = ""
    """Workspace; required with pr_number, or when ticket_id is an external id."""


class EntryActionArgs(BaseModel):
    model_config = _LENIENT

    entry_id: str
    """The entry, from loregarden_read_merge_queue."""
    action: MergeQueueAction
    """withdraw or retry."""
    #: Why it is withdrawn; kept on the entry.
    reason: str = ""
    """Why it is withdrawn; kept on the entry."""


@dataclass(frozen=True)
class Enqueued:
    admission: Admission
    entry: EntryDetail


class EnqueueResponse(BaseModel):
    """The board's reply to an enqueue."""

    outcome: MergeQueueAdmission
    detail: str
    entry: EntryView


def _workspace(session: Session, slug: str) -> Workspace:
    workspace = session.exec(select(Workspace).where(Workspace.slug == slug)).first()
    if workspace is None:
        raise QueueNotFound(f"No workspace {slug}")
    return workspace


def _read(session: Session, query: QueueRead, caller: Caller) -> EntryDetail | QueueView:
    if query.entry_id:
        return entry_detail(session, query.entry_id, live=query.live)
    if not query.workspace_slug:
        raise ValueError("Pass workspace_slug for the queue, or entry_id for one entry")
    limit = query.limit if caller.transport is Transport.REST else STATUS_HISTORY
    return queue_view(session, _workspace(session, query.workspace_slug), limit=limit)


def _admit(session: Session, args: EnqueueArgs) -> Admission:
    if bool(args.ticket_id) == bool(args.pr_number):
        raise ValueError("Pass exactly one of ticket_id or pr_number")
    named = _workspace(session, args.workspace_slug) if args.workspace_slug else None
    if args.pr_number:
        if named is None:
            raise ValueError("Pass workspace_slug with pr_number")
        return enqueue_pull_request(session, named, args.pr_number)
    ticket = OrchestrationCallbackService(session).resolve_ticket(
        ticket_id=args.ticket_id, workspace_slug=args.workspace_slug or None
    )
    workspace = workspace_for_ticket(session, ticket)
    if workspace is None:
        raise ValueError(f"{ticket.external_id} has no workspace")
    if named is not None and named.id != workspace.id:
        raise ValueError(
            f"{ticket.external_id} belongs to workspace {workspace.slug}, not {named.slug}; "
            "nothing was queued"
        )
    return enqueue_ticket(session, ticket, workspace)


def _enqueue(session: Session, args: EnqueueArgs, _caller: Caller) -> Enqueued:
    admission = admitted_or_refuse(_admit(session, args))
    return Enqueued(admission, entry_detail(session, admission.entry.id, live=False))


def _act_on_entry(session: Session, args: EntryActionArgs, _caller: Caller) -> EntryDetail:
    if args.action is MergeQueueAction.WITHDRAW:
        entry = merge_queue.withdraw(session, args.entry_id, reason=args.reason)
    else:
        entry = merge_queue.retry(session, args.entry_id)
    return entry_detail(session, entry.id, live=False)


def _agent_enqueued(_session: Session, done: Enqueued) -> dict[str, Any]:
    return {
        "outcome": done.admission.outcome.value,
        "detail": done.admission.detail,
        "entry": done.entry.model_dump(mode="json"),
    }


def _page_enqueued(_session: Session, done: Enqueued) -> EnqueueResponse:
    return EnqueueResponse(
        outcome=done.admission.outcome, detail=done.admission.detail, entry=done.entry
    )


_ENQUEUE_REPLIES: Mapping[Transport, Callable[[Session, Any], Any]] = {
    Transport.MCP: _agent_enqueued,
    Transport.CLI: _agent_enqueued,
    Transport.REST: _page_enqueued,
}

MERGE_QUEUE_OPS = (
    Operation(
        id=OperationId.MERGE_QUEUE_READ,
        effect=Effect.READ,
        args=QueueRead,
        handler=_read,
    ),
    Operation(
        id=OperationId.MERGE_QUEUE_ENQUEUE,
        effect=Effect.WRITE,
        args=EnqueueArgs,
        handler=_enqueue,
        replies=_ENQUEUE_REPLIES,
    ),
    Operation(
        id=OperationId.MERGE_QUEUE_ENTRY_ACTION,
        effect=Effect.WRITE,
        args=EntryActionArgs,
        handler=_act_on_entry,
    ),
)
