"""Re-run saved research questions on their interval.

A question opts in when a person saves it with an interval (`research_saved`).
One loop wakes every `settings.research_schedule_tick_seconds` and re-runs each
saved question whose interval has passed since it last ran or was last asked to.
Counting from the ask as well as the run means a turn that recorded nothing is
re-posted next interval, not every tick.

Nothing here is silent:
- A thread with a turn in flight is skipped, and tried again on the next tick;
  that is waiting, not failing.
- A re-run that could not start is logged at warning and recorded on the
  question (`schedule_error`), which the library's research list shows.
- A re-run that starts and then fails is the thread's failed turn, shown in the
  thread like any other.

Not started in a sandbox (see `main._start_background_loops`): a snapshot's
saved questions would spend real agent turns on a copy nobody reads.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from loregarden.config import settings
from loregarden.db.session import engine
from loregarden.models.domain.enums import utcnow
from loregarden.models.domain.survey_research_tables import SurveyResearchQuestion
from loregarden.services.baxter_chat_service import BaxterChatConflictError
from loregarden.services.reference_survey.research_saved import rerun_question
from sqlmodel import Session, col, select

logger = logging.getLogger(__name__)

#: `run_due_questions` outcome for a question skipped because its thread is busy.
IN_FLIGHT = "turn in flight; next tick"
#: Outcome for a question whose re-run started.
STARTED = ""


def _as_utc(value: datetime) -> datetime:
    # SQLite hands datetimes back naive; they were written as UTC.
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def is_due(question: SurveyResearchQuestion, now: datetime) -> bool:
    if not question.saved or question.interval_days is None or question.archived_at is not None:
        return False
    marks = [_as_utc(at) for at in (question.last_run_at, question.last_scheduled_at) if at]
    if not marks:
        return True
    return max(marks) + timedelta(days=question.interval_days) <= now


def _record_failure(session: Session, question_id: str, error: str, now: datetime) -> None:
    question = session.get(SurveyResearchQuestion, question_id)
    if question is None:
        return
    question.schedule_error = error
    # Counted as asked, so a re-run that cannot start is retried next interval, not every tick.
    question.last_scheduled_at = now
    session.add(question)
    session.commit()


def run_question_if_free(session: Session, question_id: str, now: datetime) -> str:
    """One scheduled re-run. Returns STARTED, IN_FLIGHT, or the recorded error."""
    try:
        rerun_question(session, question_id, scheduled=True)
    except BaxterChatConflictError:
        session.rollback()
        return IN_FLIGHT
    except Exception as exc:  # noqa: BLE001 — recorded on the question and logged
        logger.exception("Scheduled research re-run failed to start for question %s", question_id)
        session.rollback()
        error = str(exc) or type(exc).__name__
        _record_failure(session, question_id, error, now)
        return error
    return STARTED


def run_due_questions(session: Session, now: datetime | None = None) -> dict[str, str]:
    """Re-run every due saved question. Returns question id -> outcome (blank = started)."""
    now = now or utcnow()
    due = [
        question.id
        for question in session.exec(
            select(SurveyResearchQuestion).where(
                col(SurveyResearchQuestion.saved).is_(True),
                col(SurveyResearchQuestion.interval_days).is_not(None),
                col(SurveyResearchQuestion.archived_at).is_(None),
            )
        ).all()
        if is_due(question, now)
    ]
    return {question_id: run_question_if_free(session, question_id, now) for question_id in due}


def _tick() -> dict[str, str]:
    with Session(engine) as session:
        return run_due_questions(session)


async def run_research_schedule_loop(tick_seconds: float) -> None:
    """Re-run due saved questions every `tick_seconds` until cancelled."""
    while True:
        try:
            await asyncio.sleep(tick_seconds)
            await asyncio.to_thread(_tick)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 — the loop outliving a bad tick is the point
            logger.exception("Research schedule tick failed; continuing")


def start_research_schedule_loop(tick_seconds: float | None = None) -> asyncio.Task | None:
    """Start the scheduler, or return None when the tick disables it."""
    if tick_seconds is None:
        tick_seconds = settings.research_schedule_tick_seconds
    if tick_seconds <= 0:
        logger.info("Research schedule disabled (tick=%s)", tick_seconds)
        return None
    return asyncio.create_task(run_research_schedule_loop(tick_seconds), name="research-schedule")
