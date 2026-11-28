"""A merge-queue "stopped" card closes once its pull request is queued again.

A stopped entry files a card asking for a decision (`report_stopped`). The
queue hides that entry as soon as the same PR is queued again
(`is_superseded`), but until lg-milestone-that-2821 the card closed only on a
merge, a closed PR or a resolved conflict artifact. Retrying an entry or
enqueueing its PR afresh left the card asking for a decision already made.

Both writers now close it (`merge_queue.retry`, `merge_queue._insert`). This
invariant catches any card that still outlives its stop: one whose PR's newest
entry is live, merged or withdrawn. A card whose PR has no entry is left alone,
since nothing says what answers for it.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date

from loregarden.models.domain import (
    Approval,
    ApprovalKind,
    ApprovalStatus,
    InvariantReentry,
    MergeQueueEntry,
    MergeQueueState,
)
from loregarden.services.invariants import Invariant, Violation
from loregarden.services.merge_queue_cards import QUEUE_CARD_PREFIX, close_card
from sqlmodel import Session, col, select

#: The states whose card is still the PR's current word.
_STILL_STOPPED = (MergeQueueState.FAILED, MergeQueueState.TIMED_OUT)
_PR_MARK = f"{QUEUE_CARD_PREFIX}PR #"


def _card_pr_number(card: Approval) -> int | None:
    number = card.title.removeprefix(_PR_MARK)
    return int(number) if number.isdigit() else None


def _newest_entry(session: Session, workspace_id: str, pr_number: int) -> MergeQueueEntry | None:
    return session.exec(
        select(MergeQueueEntry)
        .where(MergeQueueEntry.workspace_id == workspace_id)
        .where(MergeQueueEntry.pr_number == pr_number)
        .order_by(col(MergeQueueEntry.position).desc())
    ).first()


def _stale(session: Session) -> list[tuple[Approval, MergeQueueEntry]]:
    cards = session.exec(
        select(Approval)
        .where(Approval.kind == ApprovalKind.HUMAN_ACTION)
        .where(Approval.status == ApprovalStatus.PENDING)
        .where(col(Approval.title).startswith(_PR_MARK))
    ).all()
    stale: list[tuple[Approval, MergeQueueEntry]] = []
    for card in cards:
        pr_number = _card_pr_number(card)
        if pr_number is None:
            continue  # silent-ok: not a queue card's title shape; report_stopped writes only PR #<n>
        newest = _newest_entry(session, card.workspace_id, pr_number)
        if newest is not None and newest.state not in _STILL_STOPPED:
            stale.append((card, newest))
    return stale


def stale_stopped_cards(session: Session) -> list[Violation]:
    return [
        Violation(
            subject_id=card.id,
            ticket_id=card.ticket_id,
            # The newest entry was admitted (or moved) at its state change; the
            # card has been stale since at least then.
            entered_at=newest.state_changed_at,
            detail=f"{card.title} is still open, but PR #{newest.pr_number}'s newest entry "
            f"is {newest.state.value}: {newest.detail}",
        )
        for card, newest in _stale(session)
    ]


def close_stale_stopped_cards(session: Session, violations: Sequence[Violation]) -> int:
    wanted = {violation.subject_id for violation in violations}
    closed = 0
    # Re-detected: a card closed since detection is not closed twice.
    for card, newest in _stale(session):
        if card.id in wanted:
            closed += close_card(
                session, newest, reason=f"PR #{newest.pr_number} is {newest.state.value}"
            )
    return closed


STOPPED_CARDS_CLOSE_WHEN_QUEUED_AGAIN = Invariant(
    name="stopped_cards_close_when_queued_again",
    ticket="lg-milestone-that-2821",
    statement=(
        "no merge-queue 'stopped' card stays open once its pull request's newest entry "
        "is no longer failed or timed out"
    ),
    detect=stale_stopped_cards,
    repair=close_stale_stopped_cards,
    reentry=InvariantReentry.DEFECT,
    since=date(2026, 11, 28),
    prevented_by=(
        "server/tests/test_merge_queue_stale_cards.py"
        "::test_every_writer_that_queues_a_pr_again_closes_its_stopped_card"
    ),
)
