"""The merge queue's admission, ordering and withdrawal (lg-milestone-that-968).

One candidate per workspace is active at a time, FIFO by position; the loop
(`merge_queue_loop`) drives it. This module decides who joins, in what order,
and who leaves, and holds the compare-and-swap every state change goes through
so the loop never acts on an entry someone has just withdrawn.

**Ordering.** A position is the next number after the highest the workspace
has ever used, claimed by the insert itself: ``(workspace_id, position)`` is
unique, so of two admissions that read the same maximum one insert wins and
the other is refused by the database and re-reads. The claim is the row, and
no state is written anywhere before it — the ordering the promotion lock
taught (book the counter, then CAS the row; never use a shared intermediate
state as a private lock).

**Admission is never a bare False.** Every refusal names its reason as a
`MergeQueueAdmission`, because "no pull request" and "already queued" lead an
operator to different actions.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from loregarden.models.domain import (
    MERGE_QUEUE_ACTIVE_STATES,
    MERGE_QUEUE_TERMINAL_STATES,
    MergeQueueAdmission,
    MergeQueueEntry,
    MergeQueueState,
    MergeQueueWait,
    Ticket,
    Workspace,
    utcnow,
)
from sqlalchemy import func, update
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, col, select

logger = logging.getLogger(__name__)

#: How many times an admission re-reads the next position after losing a race
#: for it. Each loss means another admission committed, so this bounds how many
#: simultaneous admissions one caller outlasts, not a retry on failure.
_POSITION_ATTEMPTS = 16


@dataclass(frozen=True)
class Candidate:
    """What a pull request offers the queue: its number, its head, and the work it carries."""

    pr_number: int
    pr_url: str
    pr_head_branch: str
    source_branch: str
    head_sha: str
    base_branch: str


@dataclass(frozen=True)
class Admission:
    outcome: MergeQueueAdmission
    detail: str
    entry: MergeQueueEntry | None = None

    @property
    def admitted(self) -> bool:
        return self.outcome in (MergeQueueAdmission.ADMITTED, MergeQueueAdmission.UPDATED)


class Step(StrEnum):
    CUT = "cut"
    PUBLISH = "publish"
    WAIT = "wait"
    LAND = "land"
    CLEAN_UP = "clean_up"


@dataclass(frozen=True)
class StepSpec:
    #: Whether the step writes anything another process can see.
    mutates_shared_state: bool
    #: Whether a newer cycle, or an operator, may abandon it part-way.
    cancellable: bool
    why: str


STEPS: dict[Step, StepSpec] = {
    Step.CUT: StepSpec(
        mutates_shared_state=False,
        cancellable=True,
        why="writes an unreferenced commit object; a newer cut supersedes it",
    ),
    Step.PUBLISH: StepSpec(
        mutates_shared_state=True,
        cancellable=False,
        why="moves the PR's head on GitHub, which starts its checks",
    ),
    Step.WAIT: StepSpec(
        mutates_shared_state=False,
        cancellable=True,
        why="only reads checks; the next cycle reads them again",
    ),
    Step.LAND: StepSpec(
        mutates_shared_state=True,
        cancellable=False,
        why="asks GitHub to move the base",
    ),
    Step.CLEAN_UP: StepSpec(
        mutates_shared_state=True,
        cancellable=False,
        why="deletes the staging ref on GitHub",
    ),
}


#: The step an entry in each active state is in. Withdrawal follows the step's
#: declaration: a state whose step mutates shared state cannot be withdrawn.
STATE_STEP: dict[MergeQueueState, Step] = {
    MergeQueueState.STAGING: Step.CUT,
    MergeQueueState.TESTING: Step.WAIT,
    MergeQueueState.MERGING: Step.LAND,
}


class QueueRefusal(ValueError):
    """A withdraw or retry the entry's state does not allow; nothing was changed."""


def live_entries(session: Session, workspace_id: str) -> list[MergeQueueEntry]:
    """Every entry not yet terminal, in queue order."""
    return list(
        session.exec(
            select(MergeQueueEntry)
            .where(MergeQueueEntry.workspace_id == workspace_id)
            .where(col(MergeQueueEntry.state).not_in(list(MERGE_QUEUE_TERMINAL_STATES)))
            .order_by(col(MergeQueueEntry.position))
        ).all()
    )


def entries_for(session: Session, workspace_id: str, *, limit: int = 50) -> list[MergeQueueEntry]:
    """The live entries in order, then the most recent finished ones, newest first."""
    finished = session.exec(
        select(MergeQueueEntry)
        .where(MergeQueueEntry.workspace_id == workspace_id)
        .where(col(MergeQueueEntry.state).in_(list(MERGE_QUEUE_TERMINAL_STATES)))
        .order_by(col(MergeQueueEntry.state_changed_at).desc())
        .limit(limit)
    ).all()
    return [*live_entries(session, workspace_id), *finished]


def head_entry(session: Session, workspace_id: str) -> MergeQueueEntry | None:
    """The entry the loop acts on: the active one, else the first queued."""
    live = live_entries(session, workspace_id)
    active = [entry for entry in live if entry.state in MERGE_QUEUE_ACTIVE_STATES]
    if active:
        return active[0]
    return live[0] if live else None


def candidate_label(ticket: Ticket | None, pr_number: int) -> str:
    """How a candidate is named in words: its ticket, else its pull request."""
    return ticket.external_id if ticket is not None else f"PR #{pr_number}"


def enqueue(
    session: Session, ticket: Ticket | None, workspace: Workspace, candidate: Candidate
) -> Admission:
    """Admit ``candidate`` for ``ticket`` (None for a PR admitted by number), or say why not."""
    label = candidate_label(ticket, candidate.pr_number)
    if candidate.pr_number <= 0:
        return Admission(
            MergeQueueAdmission.NO_PULL_REQUEST,
            f"{label} has no open pull request to queue; publish it first",
        )
    if not candidate.head_sha:
        return Admission(
            MergeQueueAdmission.NO_PULL_REQUEST,
            f"PR #{candidate.pr_number} for {label} names no head commit",
        )
    duplicate = _live_with_head(session, workspace.id, candidate.head_sha)
    if duplicate is not None:
        return Admission(
            MergeQueueAdmission.DUPLICATE_HEAD,
            f"{candidate.head_sha[:12]} is already queued as PR #{duplicate.pr_number} "
            f"({duplicate.state.value})",
            duplicate,
        )
    same_pr = _live_for_pr(session, workspace.id, candidate.pr_number)
    if same_pr is not None and same_pr.state is not MergeQueueState.MERGING:
        return _take_new_head(session, same_pr, candidate)
    return _insert(session, ticket, workspace, candidate)


def _take_new_head(session: Session, entry: MergeQueueEntry, candidate: Candidate) -> Admission:
    """Point an entry that is not yet merging at newer work on the same PR.

    It keeps its place: the PR was already waiting its turn, and the new work
    is the same candidate a landing later. Whatever was staged tested the old
    head, so it is discarded and the entry stages again from the start.
    """
    previous = entry.head_sha
    moved = transition(
        session,
        entry,
        entry.state,
        state=MergeQueueState.QUEUED,
        wait=MergeQueueWait.NONE,
        head_sha=candidate.head_sha,
        pr_url=candidate.pr_url or entry.pr_url,
        pr_head_branch=candidate.pr_head_branch or entry.pr_head_branch,
        source_branch=candidate.source_branch or entry.source_branch,
        staged_sha="",
        staged_tree="",
        staged_base_sha="",
        testing_started_at=None,
        detail=f"new work {candidate.head_sha[:12]} replaced {previous[:12]}; staging again",
    )
    if not moved:
        # The loop moved it in the meantime (to merging, or to a terminal
        # state). Admit the new head as its own candidate instead.
        return enqueue(session, entry_ticket(session, entry), _workspace(session, entry), candidate)
    session.refresh(entry)
    return Admission(MergeQueueAdmission.UPDATED, entry.detail, entry)


def entry_ticket(session: Session, entry: MergeQueueEntry) -> Ticket | None:
    """The entry's ticket; None for a PR admitted by number."""
    return session.get(Ticket, entry.ticket_id) if entry.ticket_id else None


def _workspace(session: Session, entry: MergeQueueEntry) -> Workspace:
    workspace = session.get(Workspace, entry.workspace_id)
    if workspace is None:
        raise LookupError(f"merge queue entry {entry.id} names a missing workspace")
    return workspace


def _insert(
    session: Session, ticket: Ticket | None, workspace: Workspace, candidate: Candidate
) -> Admission:
    for _ in range(_POSITION_ATTEMPTS):
        entry = MergeQueueEntry(
            workspace_id=workspace.id,
            ticket_id=ticket.id if ticket is not None else None,
            pr_number=candidate.pr_number,
            pr_url=candidate.pr_url,
            pr_head_branch=candidate.pr_head_branch,
            source_branch=candidate.source_branch,
            head_sha=candidate.head_sha,
            base_branch=candidate.base_branch,
            position=_next_position(session, workspace.id),
            detail="waiting its turn",
        )
        session.add(entry)
        try:
            session.commit()
        except IntegrityError:
            session.rollback()
            duplicate = _live_with_head(session, workspace.id, candidate.head_sha)
            if duplicate is not None:
                return Admission(
                    MergeQueueAdmission.DUPLICATE_HEAD,
                    f"{candidate.head_sha[:12]} was queued by a concurrent admission "
                    f"as PR #{duplicate.pr_number}",
                    duplicate,
                )
            continue  # lost the position to a concurrent admission; read the next one
        session.refresh(entry)
        logger.info(
            "Merge queue %s: admitted PR #%s (%s) at position %s",
            workspace.slug,
            entry.pr_number,
            candidate_label(ticket, entry.pr_number),
            entry.position,
        )
        return Admission(
            MergeQueueAdmission.ADMITTED, f"queued at position {entry.position}", entry
        )
    raise RuntimeError(
        f"could not claim a merge queue position in {workspace.slug} after "
        f"{_POSITION_ATTEMPTS} concurrent admissions"
    )


def _next_position(session: Session, workspace_id: str) -> int:
    highest = session.exec(
        select(func.max(MergeQueueEntry.position)).where(
            MergeQueueEntry.workspace_id == workspace_id
        )
    ).one()
    return (highest or 0) + 1


def _live_with_head(session: Session, workspace_id: str, head_sha: str) -> MergeQueueEntry | None:
    return next(
        (entry for entry in live_entries(session, workspace_id) if entry.head_sha == head_sha),
        None,
    )


def _live_for_pr(session: Session, workspace_id: str, pr_number: int) -> MergeQueueEntry | None:
    return next(
        (entry for entry in live_entries(session, workspace_id) if entry.pr_number == pr_number),
        None,
    )


def transition(
    session: Session, entry: MergeQueueEntry, expected: MergeQueueState, **fields: Any
) -> bool:
    """Move ``entry`` from ``expected``, or report that someone else moved it first.

    A compare-and-swap on the row's state: the update matches only while the
    row still reads ``expected``. False means it did not — withdrawn by an
    operator, re-pointed by a new publish — and nothing was written. That is a
    named outcome callers handle, never an error to log and continue past.
    ``fields`` are column values, checked against the model so a misspelled
    name fails here rather than writing nothing.
    """
    unknown = set(fields) - set(MergeQueueEntry.model_fields)
    if unknown:
        raise TypeError(f"not merge queue entry columns: {sorted(unknown)}")
    values = dict(fields)
    if values.get("state", expected) is not expected:
        values["state_changed_at"] = utcnow()
    result = session.execute(
        update(MergeQueueEntry)
        .where(col(MergeQueueEntry.id) == entry.id)
        .where(col(MergeQueueEntry.state) == expected)
        .values(**values)
    )
    session.commit()
    session.expire(entry)
    return result.rowcount == 1


def withdraw(session: Session, entry_id: str, *, reason: str = "") -> MergeQueueEntry:
    """Take an entry out of the queue. Refused once it is merging or finished.

    Merging is uncancellable: GitHub has been asked to move the base, and an
    entry marked withdrawn while that merge lands would describe a landing
    that happened as one that did not.
    """
    entry = _entry(session, entry_id)
    if entry.state in MERGE_QUEUE_TERMINAL_STATES:
        raise QueueRefusal(f"PR #{entry.pr_number} is already {entry.state.value}")
    step = STATE_STEP.get(entry.state)
    if step is not None and not STEPS[step].cancellable:
        raise QueueRefusal(
            f"PR #{entry.pr_number} is {entry.state.value} ({STEPS[step].why}); "
            "it cannot be withdrawn now"
        )
    detail = f"withdrawn: {reason}" if reason else "withdrawn"
    if not transition(
        session,
        entry,
        entry.state,
        state=MergeQueueState.WITHDRAWN,
        wait=MergeQueueWait.NONE,
        detail=detail,
    ):
        raise QueueRefusal(f"PR #{entry.pr_number} moved while being withdrawn; read it again")
    session.refresh(entry)
    return entry


def retry(session: Session, entry_id: str) -> MergeQueueEntry:
    """Send a failed, timed-out or withdrawn entry to the back of the queue."""
    entry = _entry(session, entry_id)
    if entry.state not in (
        MergeQueueState.FAILED,
        MergeQueueState.TIMED_OUT,
        MergeQueueState.WITHDRAWN,
    ):
        raise QueueRefusal(
            f"PR #{entry.pr_number} is {entry.state.value}; only a failed, timed-out or "
            "withdrawn entry can be retried"
        )
    duplicate = _live_with_head(session, entry.workspace_id, entry.head_sha)
    if duplicate is not None:
        raise QueueRefusal(
            f"{entry.head_sha[:12]} is already queued again as PR #{duplicate.pr_number}"
        )
    for _ in range(_POSITION_ATTEMPTS):
        try:
            moved = transition(
                session,
                entry,
                entry.state,
                state=MergeQueueState.QUEUED,
                wait=MergeQueueWait.NONE,
                position=_next_position(session, entry.workspace_id),
                staged_sha="",
                staged_tree="",
                staged_base_sha="",
                testing_started_at=None,
                artifact_ref="",
                detail="retried; waiting its turn",
            )
        except IntegrityError:
            session.rollback()
            continue
        if not moved:
            raise QueueRefusal(f"PR #{entry.pr_number} moved while being retried; read it again")
        session.refresh(entry)
        return entry
    raise RuntimeError(f"could not claim a merge queue position for PR #{entry.pr_number}")


def _entry(session: Session, entry_id: str) -> MergeQueueEntry:
    entry = session.get(MergeQueueEntry, entry_id)
    if entry is None:
        raise LookupError(f"no merge queue entry {entry_id}")
    return entry
