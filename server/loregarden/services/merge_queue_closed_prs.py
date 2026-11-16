"""A stopped merge-queue entry whose pull request was closed has nothing left to wait for.

A failed or timed-out entry stays on Home and the Lanes page as "stopped and
waiting on someone" until its PR is queued again (`_later_entry_for_same_work`).
A PR closed or merged outside the queue after its entry stopped never is, so
the entry waited forever: withdraw refuses a finished entry and retry would
stage a closed PR. PR #879 sat there after #880 landed its content.

GitHub closes the PR, not a writer here, so no guard can stop the state being
entered; the invariant settles it on the reconcile timer instead.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from datetime import date

from loregarden.models.domain import InvariantReentry, MergeQueueEntry, MergeQueueState, Workspace
from loregarden.models.domain.pull_request_enums import PullRequestState
from loregarden.services.github_pr_listing import GitHubReadError
from loregarden.services.invariants import Invariant, Violation
from loregarden.services.merge_queue import is_superseded, transition
from loregarden.services.merge_queue_github import GhQueueGitHub, QueueGitHub
from loregarden.services.merge_queue_reports import close_card
from loregarden.services.workspace_paths import resolve_workspace_root
from sqlmodel import Session, col, select

logger = logging.getLogger(__name__)

#: Stopped and waiting on someone. A withdrawal was someone's choice already.
_WAITING = (MergeQueueState.FAILED, MergeQueueState.TIMED_OUT)


def github_for(workspace: Workspace) -> QueueGitHub:
    return GhQueueGitHub(resolve_workspace_root(workspace))


def _waiting_entries(session: Session) -> list[MergeQueueEntry]:
    entries = session.exec(
        select(MergeQueueEntry)
        .where(col(MergeQueueEntry.state).in_(list(_WAITING)))
        .where(MergeQueueEntry.pr_number > 0)
        .order_by(col(MergeQueueEntry.position))
    ).all()
    # A later entry for the same PR answers for it (`entries_for` hides this one).
    return [entry for entry in entries if not is_superseded(session, entry)]


def _closed_state(github: QueueGitHub, entry: MergeQueueEntry) -> PullRequestState | None:
    """The PR's state when it is no longer open; None when open or unreadable."""
    try:
        state = github.read_pull_request(entry.pr_number).state
    except GitHubReadError as exc:
        # silent-ok: retryable; the entry is unchanged and the next pass asks again
        logger.warning(
            "Merge queue: could not read PR #%s for stopped entry %s: %s",
            entry.pr_number,
            entry.id,
            exc,
        )
        return None
    return None if state is PullRequestState.OPEN else state


def stopped_entries_with_closed_prs(session: Session) -> list[Violation]:
    violations: list[Violation] = []
    githubs: dict[str, QueueGitHub] = {}
    for entry in _waiting_entries(session):
        workspace = session.get(Workspace, entry.workspace_id)
        if workspace is None:
            continue  # silent-ok: workspace FK; a deleted workspace takes its entries with it
        github = githubs.setdefault(workspace.id, github_for(workspace))
        state = _closed_state(github, entry)
        if state is None:
            continue
        violations.append(
            Violation(
                subject_id=entry.id,
                ticket_id=entry.ticket_id,
                entered_at=entry.state_changed_at,
                detail=f"PR #{entry.pr_number} was {state.value} after the queue stopped it "
                f"({entry.state.value}): {entry.detail}",
            )
        )
    return violations


def withdraw_stopped_entries(session: Session, violations: Sequence[Violation]) -> int:
    settled = 0
    for violation in violations:
        entry = session.get(MergeQueueEntry, violation.subject_id)
        if entry is None or entry.state not in _WAITING:
            continue  # silent-ok: retried or withdrawn since detection; nothing waits on it now
        if not transition(
            session,
            entry,
            entry.state,
            state=MergeQueueState.WITHDRAWN,
            detail=f"withdrawn: {violation.detail}",
        ):
            continue  # silent-ok: someone moved it first; the next pass re-detects if still waiting
        session.refresh(entry)
        close_card(session, entry, reason=f"PR #{entry.pr_number} closed; entry withdrawn")
        settled += 1
    return settled


STOPPED_ENTRIES_OUTLIVE_NO_CLOSED_PR = Invariant(
    name="stopped_entries_outlive_no_closed_pr",
    ticket="lg-milestone-that-2641",
    statement="no failed or timed-out merge-queue entry waits on a pull request that is closed",
    detect=stopped_entries_with_closed_prs,
    repair=withdraw_stopped_entries,
    reentry=InvariantReentry.EXPECTED,
    since=date(2026, 11, 16),
    expected_because=(
        "a person closes or merges a PR on GitHub after the queue stopped its entry — "
        "#879 was closed once #880 landed its content — and no writer here sees that happen"
    ),
)
