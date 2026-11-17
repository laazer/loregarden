"""HTTP surface for the merge queue (lg-milestone-that-970).

Beside the lane routes, in their shape: Pydantic request bodies, `HTTPException`
for refusals. The board reads the queue and drives it from here; agents use the
MCP tools, and the reads, the enqueue and the entry moves are operations
(`services/operations/merge_queue_ops.py`) both run, so they cannot disagree.

A read that has to ask GitHub and cannot is a 502 naming what failed, never an
empty answer. A refusal the queue gives on purpose (no open PR, an entry that
is merging) is a 409 carrying the queue's own reason.
"""

from __future__ import annotations

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from loregarden.api.operation_routes import run_rest_operation
from loregarden.db.session import get_session
from loregarden.models.domain import MergeQueueAction, Ticket, Workspace
from loregarden.services.merge_queue_requests import (
    EntryDetail,
    EntryView,
    QueueOverview,
    QueueView,
    TicketQueueView,
    queue_overview,
    ticket_queue_view,
)
from loregarden.services.operations import OperationId
from loregarden.services.operations.merge_queue_ops import EnqueueResponse
from loregarden.services.pr_cycle import PrCycleView, pr_cycle_view
from pydantic import BaseModel, Field, model_validator
from sqlmodel import Session, select

router = APIRouter(prefix="/api/merge-queue", tags=["merge-queue"])


class EnqueueRequest(BaseModel):
    """Exactly one of the two: a tree's PR by its ticket, or any other PR by number."""

    #: Any ticket in the tree; its root's PR is what is queued.
    ticket_id: str = ""
    #: A pull request with no ticket behind it, such as an interactive session's.
    pr_number: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def _one_candidate(self) -> EnqueueRequest:
        if bool(self.ticket_id) == bool(self.pr_number):
            raise ValueError("pass exactly one of ticket_id or pr_number")
        return self


class WithdrawRequest(BaseModel):
    #: Kept on the entry, so whoever finds it withdrawn knows why.
    reason: str = ""


def _workspace(session: Session, slug: str) -> Workspace:
    workspace = session.exec(select(Workspace).where(Workspace.slug == slug)).first()
    if workspace is None:
        raise HTTPException(404, f"No workspace {slug}")
    return workspace


@router.get("", response_model=QueueOverview)
def read_merge_queues(
    limit: int = Query(10, ge=1, le=100, description="Finished entries per workspace."),
    session: Session = Depends(get_session),
) -> QueueOverview:
    """Every workspace's queue that has held an entry: what is landing anywhere."""
    return queue_overview(session, limit=limit)


@router.get("/tickets/{ticket_id}", response_model=TicketQueueView)
def read_ticket_merge_queue(
    ticket_id: str, session: Session = Depends(get_session)
) -> TicketQueueView:
    """Where a ticket's tree stands in the queue: its latest entry, or none."""
    ticket = session.get(Ticket, ticket_id)
    if ticket is None:
        raise HTTPException(404, f"No ticket {ticket_id}")
    return ticket_queue_view(session, ticket)


@router.get("/{slug}", response_model=QueueView)
def read_merge_queue(
    slug: str,
    limit: int = Query(50, ge=1, le=500, description="Finished entries to include."),
    session: Session = Depends(get_session),
) -> QueueView:
    """The live entries in queue order, then the most recent finished ones."""
    return run_rest_operation(
        session, OperationId.MERGE_QUEUE_READ, {"workspace_slug": slug, "limit": limit}
    )


@router.get("/entries/{entry_id}", response_model=EntryDetail)
def read_merge_queue_entry(
    entry_id: str,
    live: bool = Query(True, description="Read a testing entry's checks from GitHub now."),
    session: Session = Depends(get_session),
) -> EntryDetail:
    """One entry, with the required checks it is waiting on."""
    return run_rest_operation(
        session, OperationId.MERGE_QUEUE_READ, {"entry_id": entry_id, "live": live}
    )


@router.get("/{slug}/pr-cycle", response_model=PrCycleView)
def read_pr_cycle(
    slug: str,
    branch: str = Query(..., min_length=1),
    pr_number: int | None = Query(None, gt=0),
    session: Session = Depends(get_session),
) -> PrCycleView:
    """A branch's PR, live from GitHub, and its latest queue entry (the `pr_cycle` card).

    A failed GitHub read is a 200 whose `pull_request.lookup` is `failed` with
    `gh`'s words, as the ticket PR tab reads it: the card shows the reason
    beside what it still knows (the recorded pre-push result).
    """
    return pr_cycle_view(session, _workspace(session, slug), branch=branch, pr_number=pr_number)


@router.post("/{slug}/entries", response_model=EnqueueResponse)
def enqueue_merge(
    slug: str,
    body: EnqueueRequest = Body(...),
    session: Session = Depends(get_session),
) -> EnqueueResponse:
    """Queue a tree's open PR by any ticket in it, or any other open PR by number.

    A ticket that is not in `slug`'s workspace is refused, never queued there.
    """
    return run_rest_operation(
        session,
        OperationId.MERGE_QUEUE_ENQUEUE,
        {"workspace_slug": slug, "ticket_id": body.ticket_id, "pr_number": body.pr_number},
    )


@router.post("/entries/{entry_id}/withdraw", response_model=EntryView)
def withdraw_merge(
    entry_id: str,
    body: WithdrawRequest = Body(default_factory=WithdrawRequest),
    session: Session = Depends(get_session),
) -> EntryView:
    """Take an entry out of the queue. Refused (409) once it is merging or finished."""
    return run_rest_operation(
        session,
        OperationId.MERGE_QUEUE_ENTRY_ACTION,
        {"entry_id": entry_id, "action": MergeQueueAction.WITHDRAW, "reason": body.reason},
    )


@router.post("/entries/{entry_id}/retry", response_model=EntryView)
def retry_merge(entry_id: str, session: Session = Depends(get_session)) -> EntryView:
    """Send a failed, timed-out or withdrawn entry to the back of the queue."""
    return run_rest_operation(
        session,
        OperationId.MERGE_QUEUE_ENTRY_ACTION,
        {"entry_id": entry_id, "action": MergeQueueAction.RETRY},
    )
