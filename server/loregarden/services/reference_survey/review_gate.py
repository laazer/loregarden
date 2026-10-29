"""Whether a review can start on a source now, before anything is written.

Kept free of the feeder and the queue so the inbox view can ask it too
(`card_view`) without importing the dispatch machinery.
"""

from __future__ import annotations

from loregarden.models.domain.survey_enums import SurveyPassStatus, SurveySourceStatus
from loregarden.models.domain.survey_tables import SurveyPass, SurveySource
from loregarden.services.reference_survey.errors import SurveyError, brief_reason
from sqlmodel import Session, select


def refuse_unrunnable(session: Session, source: SurveySource, reason: str) -> None:
    """Refuse, before anything is written, a source no review can start on now."""
    if source.status is SurveySourceStatus.REFUSED and not reason.strip():
        raise SurveyError(
            f"{source.name} was refused ({brief_reason(source.refused_reason)}). "
            "Say why it should be read after all to review it again."
        )
    if source.status is SurveySourceStatus.PROPOSED:
        raise SurveyError(
            f"{source.name} has not been admitted: approve its card in the inbox first."
            if source.approval_id
            else f"{source.name} has not been proposed: add it to the queue first."
        )
    reading = session.exec(
        select(SurveyPass).where(
            SurveyPass.source_id == source.id, SurveyPass.status == SurveyPassStatus.DISPATCHED
        )
    ).first()
    if reading is not None:
        raise SurveyError(f"Pass {reading.ordinal} is reading {source.name} now.")


def review_blocker(session: Session, source: SurveySource) -> str:
    """Why a review with no reason given cannot start on ``source`` now, or ""."""
    try:
        refuse_unrunnable(session, source, "")
    except SurveyError as exc:
        return str(exc)
    return ""
