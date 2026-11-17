"""Merging a ticket's pull request and cleaning up after it, as an operation.

The PR tab's "Merge and clean up" (`POST /api/tickets/{id}/pull-request/merge`)
and `loregarden_merge_pull_request` were the same service call written twice.
The tool flattened a failed merge into a `ValueError`, the type a refusal has,
so a caller could not tell "GitHub would not merge it" from "nothing was
tried". One handler now lets `MergeRefused` (nothing was attempted) and
`MergeFailed` (GitHub was asked and did not merge) through on every transport;
REST words them 409 and 502, MCP and the CLI report both as errors.

What stays the transport's: the page sends the head it showed the operator
and is pinned to it, always; an agent may omit ``head_sha`` and is pinned to
the head as it stands at the call.
"""

from __future__ import annotations

from loregarden.services.operations.spec import (
    Caller,
    Effect,
    Operation,
    OperationId,
    Transport,
)
from loregarden.services.orchestration_callbacks import OrchestrationCallbackService
from loregarden.services.pull_request_merge import (
    MergeAndCleanUp,
    MergeFailed,
    merge_and_clean_up,
)
from loregarden.services.ticket_pull_request import ticket_pull_request
from loregarden.services.ticket_workspace_binding import workspace_for_ticket
from pydantic import BaseModel, ConfigDict
from sqlmodel import Session


class PullRequestMergeArgs(BaseModel):
    model_config = ConfigDict(extra="ignore")

    ticket_id: str
    number: int
    #: The head the caller checked. Required on REST (its request model says
    #: so); an agent's empty one means the head as it stands now.
    head_sha: str = ""


def _merge(session: Session, args: PullRequestMergeArgs, caller: Caller) -> MergeAndCleanUp:
    ticket = OrchestrationCallbackService(session).resolve_ticket(ticket_id=args.ticket_id)
    workspace = workspace_for_ticket(session, ticket)
    if workspace is None:
        raise ValueError(f"Ticket {ticket.external_id} has no workspace")
    head_sha = args.head_sha
    if not head_sha and caller.transport is not Transport.REST:
        current = ticket_pull_request(session, ticket, workspace).pull_request
        head_sha = current.head_sha if current else ""
    try:
        return merge_and_clean_up(session, ticket, workspace, number=args.number, head_sha=head_sha)
    except MergeFailed as exc:
        raise MergeFailed(f"Merge of #{args.number} failed: {exc}") from exc


PULL_REQUEST_MERGES = (
    Operation(
        id=OperationId.TICKET_PULL_REQUEST_MERGE,
        effect=Effect.WRITE,
        args=PullRequestMergeArgs,
        handler=_merge,
    ),
)
