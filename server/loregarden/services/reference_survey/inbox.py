"""The survey's two kinds of inbox card: admitting a source, and an exception.

The queue drains unattended. A person is asked for exactly two things: whether a
new source may be read at all, and what to do about something that went wrong
(a licence refusal, a pass that stopped without recording where, a ticket write
whose read-back disagreed). Everything else runs without them.

Each card is keyed by its kind and source (`SurveyCardKey`, kept in
`tool_input_json`), so the survey can find its own cards again: to avoid filing
a second one, and to close one once its cause has cleared (`close_cleared`). A
card that outlives its cause tells the operator to fix something already fixed.
"""

from __future__ import annotations

import logging
from collections.abc import Collection, Sequence
from dataclasses import dataclass
from datetime import datetime

from loregarden.core.event_bus import event_bus
from loregarden.models.domain import Approval, ApprovalKind, ApprovalStatus, EventType
from loregarden.models.domain.enums import utcnow
from loregarden.models.domain.survey_enums import SurveyCardKind
from loregarden.models.domain.survey_tables import SurveyPass, SurveySource
from pydantic import BaseModel, ConfigDict, ValidationError
from sqlmodel import Session, col, select

logger = logging.getLogger(__name__)

SURVEY_CARD_TOOL = "reference-survey"

#: Kinds a later pass on the same source clears by being dispatched: it cloned,
#: passed the licence gate, found a carrier parent, and is running.
CLEARED_BY_DISPATCH: frozenset[SurveyCardKind] = frozenset(
    {
        SurveyCardKind.CLONE_FAILED,
        SurveyCardKind.LICENCE_REFUSED,
        SurveyCardKind.PASS_ABANDONED,
        SurveyCardKind.NO_CARRIER_PARENT,
    }
)

#: Kinds "Retry now" acts on: `schedule.review_now` clones again, or runs a new
#: pass where the last one was abandoned. A licence refusal is not retried: the
#: same file refuses it again, and reopening one needs a person's reason.
RETRYABLE: frozenset[SurveyCardKind] = frozenset(
    {SurveyCardKind.CLONE_FAILED, SurveyCardKind.PASS_ABANDONED}
)


class SurveyCardKey(BaseModel):
    """Which card this is, independent of its wording."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: SurveyCardKind
    #: None only for a workspace-wide card (no carrier parent, a discovery run).
    source_id: str | None = None
    #: The pass or discovery run, where one source or workspace holds a card of
    #: this kind per run.
    occurrence_id: str | None = None


class _StoredKey(BaseModel):
    model_config = ConfigDict(extra="forbid")

    survey_card: SurveyCardKey


#: Cards filed before keys existed, recognised by title once so they can be
#: closed like any other. Nothing new is filed without a key.
_LEGACY_TITLE_PREFIXES: tuple[tuple[str, SurveyCardKind], ...] = (
    ("Survey clone failed: ", SurveyCardKind.CLONE_FAILED),
    ("Survey source refused by licence: ", SurveyCardKind.LICENCE_REFUSED),
    ("Licence disagreement: ", SurveyCardKind.LICENCE_DISAGREEMENT),
)
_LEGACY_NO_CARRIER_TITLE = "Reference survey has no carrier parent"


def _legacy_key(session: Session, card: Approval) -> SurveyCardKey | None:
    admitted = session.exec(select(SurveySource).where(SurveySource.approval_id == card.id)).first()
    if admitted is not None:
        return SurveyCardKey(kind=SurveyCardKind.ADMISSION, source_id=admitted.id)
    if card.title == _LEGACY_NO_CARRIER_TITLE:
        return SurveyCardKey(kind=SurveyCardKind.NO_CARRIER_PARENT)
    for prefix, kind in _LEGACY_TITLE_PREFIXES:
        if not card.title.startswith(prefix):
            continue
        name = card.title.removeprefix(prefix)
        sources = session.exec(
            select(SurveySource).where(
                SurveySource.workspace_id == card.workspace_id, SurveySource.name == name
            )
        ).all()
        if len(sources) == 1:
            return SurveyCardKey(kind=kind, source_id=sources[0].id)
        logger.warning(
            "Survey card %s names %r, which matches %d sources; it stays unkeyed",
            card.id,
            name,
            len(sources),
        )
    return None


def card_key(session: Session, card: Approval) -> SurveyCardKey | None:
    """The key of a survey card, or None for a card the survey did not file
    (or an unkeyed legacy one it cannot place)."""
    if card.tool_name != SURVEY_CARD_TOOL:
        return None
    try:
        return _StoredKey.model_validate_json(card.tool_input_json or "{}").survey_card
    except ValidationError:
        # Filed before keys: the title is all there is to go on.
        return _legacy_key(session, card)


def pending_cards(
    session: Session, workspace_id: str | None = None
) -> list[tuple[Approval, SurveyCardKey]]:
    query = select(Approval).where(
        Approval.kind == ApprovalKind.HUMAN_ACTION,
        Approval.status == ApprovalStatus.PENDING,
        Approval.tool_name == SURVEY_CARD_TOOL,
    )
    if workspace_id is not None:
        query = query.where(Approval.workspace_id == workspace_id)
    keyed = ((card, card_key(session, card)) for card in session.exec(query).all())
    return [(card, key) for card, key in keyed if key is not None]


def file_card(
    session: Session,
    *,
    workspace_id: str,
    key: SurveyCardKey,
    title: str,
    impact: str,
    level: str,
) -> Approval:
    """File a HUMAN_ACTION card, or return the pending one with the same key."""
    pending = next(
        (card for card, held in pending_cards(session, workspace_id) if held == key), None
    )
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
        tool_input_json=_StoredKey(survey_card=key).model_dump_json(),
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


def report_exception(
    session: Session, *, workspace_id: str, key: SurveyCardKey, title: str, detail: str
) -> Approval:
    """An exception a person must see: logged at warning and carded."""
    logger.warning("Reference survey: %s — %s", title, detail)
    return file_card(
        session, workspace_id=workspace_id, key=key, title=title, impact=detail, level="high"
    )


def close_cards(session: Session, cards: Sequence[Approval], *, reason: str) -> int:
    """Close each still-pending card as ignored by the survey, with ``reason``.

    Ignored, not approved: nobody decided anything; the cause went away.
    `resolved_by` says the survey closed it, so the history does not read as a
    person's click.
    """
    closing = [card for card in cards if card.status is ApprovalStatus.PENDING]
    for card in closing:
        card.status = ApprovalStatus.IGNORED
        card.resolved_by = SURVEY_CARD_TOOL
        card.resolved_at = utcnow()
        card.response_json = _StoredReason(ignored_reason=reason).model_dump_json()
        session.add(card)
    if not closing:
        return 0
    session.commit()
    for card in closing:
        logger.info("Closed survey card %s (%s): %s", card.id, card.title, reason)
        event_bus.publish(
            session,
            EventType.APPROVAL_RESOLVED,
            workspace_id=card.workspace_id,
            payload={"approval_id": card.id, "ignored": True, "reason": reason},
        )
    return len(closing)


class _StoredReason(BaseModel):
    #: The key `approval_ignore` stores a person's reason under, so the history
    #: shows the survey's reason the same way.
    ignored_reason: str


def close_cleared(
    session: Session, source: SurveySource, kinds: Collection[SurveyCardKind], *, reason: str
) -> int:
    """Close ``source``'s pending cards of ``kinds``, and the workspace-wide ones
    among ``kinds``: what they reported has stopped being true."""
    cards = [
        card
        for card, key in pending_cards(session, source.workspace_id)
        if key.kind in kinds and key.source_id in (source.id, None)
    ]
    return close_cards(session, cards, reason=reason)


@dataclass(frozen=True)
class ClearedCard:
    """A pending card whose cause a later success already cleared."""

    card: Approval
    cleared_at: datetime
    because: str


def _later_clone(session: Session, card: Approval, key: SurveyCardKey) -> ClearedCard | None:
    source = session.exec(
        select(SurveySource).where(
            SurveySource.id == key.source_id, col(SurveySource.fetched_at) > card.created_at
        )
    ).first()
    if source is None or source.fetched_at is None:
        return None
    return ClearedCard(card, source.fetched_at, f"{source.name} cloned again since")


def _later_dispatch(session: Session, card: Approval, key: SurveyCardKey) -> ClearedCard | None:
    query = (
        select(SurveyPass, SurveySource)
        .join(SurveySource, col(SurveySource.id) == SurveyPass.source_id)
        .where(col(SurveyPass.dispatched_at) > card.created_at)
    )
    query = (
        query.where(SurveyPass.source_id == key.source_id)
        if key.source_id
        else query.where(SurveySource.workspace_id == card.workspace_id)
    )
    row = session.exec(query.order_by(col(SurveyPass.dispatched_at))).first()
    if row is None or row[0].dispatched_at is None:
        return None
    survey_pass, source = row
    return ClearedCard(
        card,
        survey_pass.dispatched_at,
        f"pass {survey_pass.ordinal} on {source.name} cloned and dispatched since",
    )


def cleared_cards(session: Session) -> list[ClearedCard]:
    """Pending exception cards a later success cleared. Read-only.

    The reconciler reaches this as the `survey_cards_close_with_their_cause`
    invariant; the feeder closes the same cards as it succeeds.
    """
    found: list[ClearedCard] = []
    for card, key in pending_cards(session):
        if key.kind not in CLEARED_BY_DISPATCH:
            continue
        cleared = (
            _later_clone(session, card, key) if key.kind is SurveyCardKind.CLONE_FAILED else None
        ) or _later_dispatch(session, card, key)
        if cleared is not None:
            found.append(cleared)
    return found
