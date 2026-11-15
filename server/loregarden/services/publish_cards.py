"""Finding a tree's publish card, apart from the scheduler that files it.

Split out of `tree_publish_scheduler` so `base_delivery` can show the card
holding a landing up without importing the publish path, which moves branches
through `integration_branch_moves`, whose landing guard reads `base_delivery`.
"""

from __future__ import annotations

from loregarden.models.domain import Approval, ApprovalKind, ApprovalStatus, Ticket
from sqlmodel import Session, select

#: Every publish card's title starts with this; the sweep finds them by it.
PUBLISH_CARD_PREFIX = "Publish failed: "


def publish_card_title(root: Ticket) -> str:
    return f"{PUBLISH_CARD_PREFIX}{root.external_id or root.id[:8]}"


def pending_publish_cards(session: Session, root: Ticket) -> list[Approval]:
    return list(
        session.exec(
            select(Approval).where(
                Approval.ticket_id == root.id,
                Approval.kind == ApprovalKind.HUMAN_ACTION,
                Approval.status == ApprovalStatus.PENDING,
                Approval.title == publish_card_title(root),
            )
        ).all()
    )


def pending_publish_card(session: Session, root: Ticket) -> Approval | None:
    """The pending publish card on ``root``, if there is one."""
    cards = pending_publish_cards(session, root)
    return cards[0] if cards else None
