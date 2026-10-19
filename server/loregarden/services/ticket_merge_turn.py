"""Whether a ticket whose branch conflicts with its target may still get a merge turn.

A `conflict` block clears only by merging: the next dispatch's checkout runs
the agent's merge turn (`branch_takes_target`) when it may, and refuses at once
when it may not. So re-dispatching is worth something only while a turn may
run on the branch's current tip, which is `merge_turn_refusal`'s question
(the switch, the per-tip budget, the harness backoff), asked of this ticket's
own branch. A new tip (a person's merge, a push) starts a fresh budget.
"""

from __future__ import annotations

from loregarden.models.domain import Ticket, Workspace
from loregarden.services.git_branch import resolve_ticket_branch
from loregarden.services.git_merge_noco import rev
from loregarden.services.integration_merge_budget import merge_turn_refusal
from loregarden.services.workspace_paths import resolve_workspace_root
from sqlmodel import Session


def ticket_merge_turn_refusal(session: Session, ticket: Ticket) -> str:
    """Why no merge turn may run on this ticket's branch now; "" when one may."""
    workspace = session.get(Workspace, ticket.workspace_id) if ticket.workspace_id else None
    if workspace is None:
        return "the ticket has no workspace"
    branch = resolve_ticket_branch(ticket)
    tip = rev(resolve_workspace_root(workspace), f"refs/heads/{branch}")
    if not tip:
        return f"cannot read the tip of {branch!r}"
    return merge_turn_refusal(session, workspace, ticket, branch_sha=tip)
