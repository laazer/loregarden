"""A ticket's pull request, live from GitHub (the PR tab's data).

Its own router rather than another route in `tickets.py`, which is a hotspot.
A plain `def`: the lookup shells out to `gh`, and FastAPI runs sync handlers in
its threadpool, so a slow GitHub cannot stall the event loop.
"""

from fastapi import APIRouter, Depends, HTTPException
from loregarden.api.operation_inputs import OperationBody
from loregarden.api.operation_routes import run_rest_operation
from loregarden.db.session import get_session
from loregarden.models.domain import Ticket, Workspace
from loregarden.services.operations import OperationId
from loregarden.services.pull_request_merge import MergeAndCleanUp
from loregarden.services.ticket_ids import resolve as resolve_external_id
from loregarden.services.ticket_pull_request import TicketPullRequest, ticket_pull_request
from loregarden.services.ticket_workspace_binding import workspace_for_ticket
from sqlmodel import Session

router = APIRouter(prefix="/tickets", tags=["tickets"])


class MergeRequest(
    OperationBody,
    operation=OperationId.TICKET_PULL_REQUEST_MERGE,
    supplied={"ticket_id"},
    # The page is never merged at a head it did not send.
    required={"head_sha"},
):
    """The PR and head commit the operator was shown; the merge is pinned to both."""


def _ticket_and_workspace(session: Session, ticket_id: str) -> tuple[Ticket, Workspace]:
    ticket = session.get(Ticket, ticket_id) or resolve_external_id(session, ticket_id)
    if not ticket:
        raise HTTPException(404, "Ticket not found")
    workspace = workspace_for_ticket(session, ticket)
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
    """The operator's "Merge and clean up", pinned to the head the PR tab showed.

    Agents reach the same operation through `loregarden_merge_pull_request`,
    which is never auto-approved and is denied to pipeline stages, so an agent's
    merge still waits on a person's approval.
    """
    return run_rest_operation(
        session,
        OperationId.TICKET_PULL_REQUEST_MERGE,
        {**body.model_dump(), "ticket_id": ticket_id},
    )
