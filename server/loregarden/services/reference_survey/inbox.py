"""The survey's two kinds of inbox card: admitting a source, and an exception.

The queue drains unattended. A person is asked for exactly two things: whether a
new source may be read at all, and what to do about something that went wrong
(a licence refusal, a pass that stopped without recording where, a ticket write
whose read-back disagreed). Everything else runs without them.
"""

from __future__ import annotations

import logging

from loregarden.core.event_bus import event_bus
from loregarden.models.domain import Approval, ApprovalKind, ApprovalStatus, EventType
from sqlmodel import Session, select

logger = logging.getLogger(__name__)

SURVEY_CARD_TOOL = "reference-survey"


def file_card(
    session: Session, *, workspace_id: str, title: str, impact: str, level: str
) -> Approval:
    """File a HUMAN_ACTION card, or return the pending one with the same title."""
    pending = session.exec(
        select(Approval).where(
            Approval.workspace_id == workspace_id,
            Approval.kind == ApprovalKind.HUMAN_ACTION,
            Approval.status == ApprovalStatus.PENDING,
            Approval.title == title,
        )
    ).first()
    if pending is not None:
        return pending
    card = Approval(
        ticket_id=None,
        workspace_id=workspace_id,
        kind=ApprovalKind.HUMAN_ACTION,
        title=title,
        level=level,
        impact=impact,
        tool_name=SURVEY_CARD_TOOL,
        status=ApprovalStatus.PENDING,
    )
    session.add(card)
    session.commit()
    session.refresh(card)
    event_bus.publish(
        session,
        EventType.APPROVAL_REQUESTED,
        workspace_id=workspace_id,
        payload={"approval_id": card.id, "source": SURVEY_CARD_TOOL},
    )
    return card


def report_exception(session: Session, *, workspace_id: str, title: str, detail: str) -> Approval:
    """An exception a person must see: logged at warning and carded."""
    logger.warning("Reference survey: %s — %s", title, detail)
    return file_card(session, workspace_id=workspace_id, title=title, impact=detail, level="high")
