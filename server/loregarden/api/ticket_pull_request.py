"""A ticket's pull request, live from GitHub (the PR tab's data).

Its own router rather than another route in `tickets.py`, which is a hotspot.
A plain `def`: the lookup shells out to `gh`, and FastAPI runs sync handlers in
its threadpool, so a slow GitHub cannot stall the event loop.
"""

from fastapi import APIRouter, Depends, HTTPException
from loregarden.db.session import get_session
from loregarden.models.domain import Ticket, Workspace
from loregarden.services.pull_request_merge import (
    MergeAndCleanUp,
    MergeFailed,
    MergeRefused,
    merge_and_clean_up,
)
from loregarden.services.ticket_ids import resolve as resolve_external_id
from loregarden.services.ticket_pull_request import TicketPullRequest, ticket_pull_request
from pydantic import BaseModel
from sqlmodel import Session

router = APIRouter(prefix="/tickets", tags=["tickets"])


class MergeRequest(BaseModel):
    """The PR and head commit the operator was shown; the merge is pinned to both."""

    number: int
    head_sha: str


def _ticket_and_workspace(session: Session, ticket_id: str) -> tuple[Ticket, Workspace]:
    ticket = session.get(Ticket, ticket_id) or resolve_external_id(session, ticket_id)
    if not ticket:
        raise HTTPException(404, "Ticket not found")
    workspace = session.get(Workspace, ticket.workspace_id)
    if not workspace:
        raise HTTPException(404, "Workspace not found")
    return ticket, workspace


@router.get("/{ticket_id}/pull-request", response_model=TicketPullRequest)
def get_ticket_pull_request(
    ticket_id: str, session: Session = Depends(get_session)
) -> TicketPullRequest:
    ticket, workspace = _ticket_and_workspace(session, ticket_id)
    return ticket_pull_request(session, ticket, workspace)


@router.post("/{ticket_id}/pull-request/merge", response_model=MergeAndCleanUp)
def merge_ticket_pull_request(
    ticket_id: str, body: MergeRequest, session: Session = Depends(get_session)
) -> MergeAndCleanUp:
    """The operator's "Merge and clean up". Not an agent action: no MCP tool or UI
    action reaches it, so a merge always follows a person's click."""
    ticket, workspace = _ticket_and_workspace(session, ticket_id)
    try:
        return merge_and_clean_up(
            session, ticket, workspace, number=body.number, head_sha=body.head_sha
        )
    except MergeRefused as exc:
        raise HTTPException(409, str(exc)) from exc
    except MergeFailed as exc:
        raise HTTPException(502, str(exc)) from exc
