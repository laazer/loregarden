"""The inbox card a stopped merge-queue entry files, found and closed by its PR.

Kept apart from `merge_queue_reports` (which files the card and needs the
ticket service) so `merge_queue` itself can close a card when a PR is queued
again without importing its way back into itself.
"""

from __future__ import annotations

from loregarden.core.event_bus import event_bus
from loregarden.models.domain import (
    Approval,
    ApprovalKind,
    ApprovalStatus,
    EventType,
    MergeQueueEntry,
    utcnow,
)
from sqlmodel import Session, select

#: Every queue card's title starts with this.
QUEUE_CARD_PREFIX = "Merge queue stopped: "
RESOLVED_BY_QUEUE = "merge_queue"


def queue_card_title(entry: MergeQueueEntry) -> str:
    return f"{QUEUE_CARD_PREFIX}PR #{entry.pr_number}"


def pending_cards(session: Session, entry: MergeQueueEntry) -> list[Approval]:
    """The open stopped cards for ``entry``'s pull request."""
    return list(
        session.exec(
            select(Approval).where(
                # By workspace, not ticket: a PR admitted by number has none,
                # and `ticket_id == NULL` matches no row. The title names the PR.
                Approval.workspace_id == entry.workspace_id,
                Approval.kind == ApprovalKind.HUMAN_ACTION,
                Approval.status == ApprovalStatus.PENDING,
                Approval.title == queue_card_title(entry),
            )
        ).all()
    )


def close_card(session: Session, entry: MergeQueueEntry, *, reason: str) -> int:
    cards = pending_cards(session, entry)
    for card in cards:
        card.status = ApprovalStatus.APPROVED
        card.resolved_by = RESOLVED_BY_QUEUE
        card.resolved_at = utcnow()
        session.add(card)
    if not cards:
        return 0
    session.commit()
    for card in cards:
        event_bus.publish(
            session,
            EventType.APPROVAL_RESOLVED,
            workspace_id=entry.workspace_id,
            ticket_id=entry.ticket_id,
            payload={"approval_id": card.id, "reason": reason},
        )
    return len(cards)
