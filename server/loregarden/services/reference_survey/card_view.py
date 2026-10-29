"""What the inbox needs to render a survey card's own actions.

An exception card's Approve and Reject would only close it, so the inbox offers
what exists instead: Retry now (`schedule.review_now`) where it can act,
Dismiss, and the source itself.
"""

from __future__ import annotations

from loregarden.models.domain import Approval
from loregarden.models.domain.survey_enums import SurveyCardKind
from loregarden.models.domain.survey_tables import SurveySource
from loregarden.services.reference_survey.inbox import RETRYABLE, card_key
from loregarden.services.reference_survey.review_gate import review_blocker
from pydantic import BaseModel
from sqlmodel import Session


class SurveyCardView(BaseModel):
    kind: SurveyCardKind
    #: None for a workspace-wide card, or a source since deleted.
    source_id: str | None
    source_name: str
    #: Retry now applies to this kind of card and its source still exists.
    retryable: bool
    #: Why Retry now cannot start at the moment ("" when it can, or it does not apply).
    retry_held: str


def survey_card_view(session: Session, approval: Approval) -> SurveyCardView | None:
    """The survey's view of ``approval``, or None when the survey did not file it."""
    key = card_key(session, approval)
    if key is None:
        return None
    source = session.get(SurveySource, key.source_id) if key.source_id else None
    retryable = source is not None and key.kind in RETRYABLE
    return SurveyCardView(
        kind=key.kind,
        source_id=source.id if source else None,
        source_name=source.name if source else "",
        retryable=retryable,
        retry_held=review_blocker(session, source) if retryable and source else "",
    )
