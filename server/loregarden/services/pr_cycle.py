"""Where a branch stands on its way to the base: its PR and its merge-queue entry.

The `pr_cycle` chat card reads this. Pre-push is not here: nothing can read a
past push's hook result back, so the card's writer records it on the part.
"""

from __future__ import annotations

from loregarden.models.domain import PullRequestLookup, Workspace
from loregarden.services.merge_queue_requests import EntryView, pull_request_queue_entry
from loregarden.services.ticket_pull_request import PullRequestRead, read_pull_request
from loregarden.services.workspace_paths import resolve_workspace_root
from pydantic import BaseModel
from sqlmodel import Session


class PrCycleView(BaseModel):
    branch: str
    #: GitHub's answer for the branch's PR; FAILED keeps "could not ask" apart from "none".
    pull_request: PullRequestRead
    #: The PR's latest merge-queue entry; None when it was never queued (or there is no PR).
    entry: EntryView | None = None


def pr_cycle_view(
    session: Session, workspace: Workspace, *, branch: str, pr_number: int | None = None
) -> PrCycleView:
    """Read the PR for ``branch`` (or ``pr_number``) live, then its queue entry."""
    repo_root = resolve_workspace_root(workspace)
    read = read_pull_request(repo_root, str(pr_number) if pr_number else branch)
    number = read.pull_request.number if read.pull_request else pr_number
    entry = (
        pull_request_queue_entry(session, workspace, number)
        if number and read.lookup is not PullRequestLookup.FAILED
        else None
    )
    return PrCycleView(branch=branch, pull_request=read, entry=entry)
