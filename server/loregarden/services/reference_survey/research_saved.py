"""Saved research questions: pin one to an area, re-run it by hand or on a schedule.

A research question is ad hoc until a person saves it. Saving lists it in the
library's research, can pin it to a project area, and can set an interval for
`research_scheduler` to re-run it. A re-run is an ordinary turn in the
question's own thread, so its reply, its cards and any failure land where the
question was asked; its prompt carries the previous run, so the agent can say
what changed and answer with the `research_diff` card.
"""

from __future__ import annotations

from loregarden.models.domain import BaxterChatSession
from loregarden.models.domain.enums import utcnow
from loregarden.models.domain.survey_area_tables import SurveyArea
from loregarden.models.domain.survey_research_tables import SurveyResearchQuestion
from loregarden.services.baxter_chat_research import is_research
from loregarden.services.baxter_chat_run_service import (
    schedule_baxter_chat_turn,
    start_baxter_chat_turn,
)
from loregarden.services.reference_survey.errors import SurveyError, SurveyNotFound
from loregarden.services.reference_survey.research import QuestionView, question_view
from pydantic import BaseModel, Field
from sqlmodel import Session

#: The longest interval a saved question can re-run at.
MAX_INTERVAL_DAYS = 90
#: How many of the previous run's candidates a re-run prompt lists.
MAX_PROMPT_CANDIDATES = 15


class SaveQuestionIn(BaseModel):
    #: False un-saves: the question drops out of the library list and stops re-running.
    saved: bool = True
    area_id: str | None = None
    interval_days: int | None = Field(default=None, ge=1, le=MAX_INTERVAL_DAYS)


class RerunStarted(BaseModel):
    question_id: str
    thread_id: str
    #: The pending assistant turn the re-run is answered in.
    turn_id: str


def _question(session: Session, question_id: str) -> SurveyResearchQuestion:
    question = session.get(SurveyResearchQuestion, question_id)
    if question is None or question.archived_at is not None:
        raise SurveyNotFound(f"No research question {question_id}")
    return question


def save_question(session: Session, question_id: str, body: SaveQuestionIn) -> QuestionView:
    """Save, re-pin or un-save a question. An area must be one of its workspace's."""
    question = _question(session, question_id)
    if body.area_id is not None:
        area = session.get(SurveyArea, body.area_id)
        if area is None or area.workspace_id != question.workspace_id:
            raise SurveyError("The area must be one of this question's workspace's project areas")
    question.saved = body.saved
    question.area_id = body.area_id if body.saved else None
    question.interval_days = body.interval_days if body.saved else None
    question.updated_at = utcnow()
    session.add(question)
    session.commit()
    return question_view(session, question.id)


def rerun_prompt(view: QuestionView, *, scheduled: bool) -> str:
    """The user turn a re-run posts: the question, the previous run, and the card to answer with."""
    who = "Scheduled re-run" if scheduled else "Re-run"
    lines = [
        f"{who} of research question {view.id} (run {view.run_count + 1}): “{view.question}”",
        "Capabilities: " + "; ".join(view.capabilities),
        "Search again for candidates and re-check each capability in source. Record every "
        f"candidate against question_id={view.id} with loregarden_research_record, the "
        "previous run's candidates included when they still qualify, so a project that is "
        "gone shows as dropped.",
    ]
    if view.candidates:
        when = f"{view.last_run_at:%Y-%m-%d}" if view.last_run_at else "unknown date"
        lines.append(f"Run {view.run_count} ({when}) recorded:")
        for candidate in view.candidates[:MAX_PROMPT_CANDIDATES]:
            claims = "; ".join(
                f"{cap}: {claim.status.value if claim else 'not checked'}"
                for cap, claim in candidate.claims.items()
            )
            lines.append(f"- {candidate.name} {candidate.url} — {claims}")
    # Named, not fenced: a fence here would render as a card inside this message,
    # before the run it shows exists. The research prompt carries the fence.
    lines.append(
        f"Answer with a research_diff card for question_id={view.id}, then at most a few "
        "sentences on what changed and why."
    )
    return "\n".join(lines)


def _research_thread(session: Session, question: SurveyResearchQuestion) -> BaxterChatSession:
    thread = session.get(BaxterChatSession, question.thread_id)
    if thread is None or not is_research(thread):
        raise SurveyError(
            "This question's thread is gone or is no longer a research thread; "
            "ask it again in a research thread"
        )
    return thread


def rerun_question(session: Session, question_id: str, *, scheduled: bool = False) -> RerunStarted:
    """Post a re-run turn to the question's thread and start it.

    Raises `BaxterChatConflictError` while the thread has a turn in flight: the
    caller decides whether that is a refusal (a person's click) or a skip (the
    schedule's next tick tries again).
    """
    question = _question(session, question_id)
    thread = _research_thread(session, question)
    prompt = rerun_prompt(question_view(session, question.id), scheduled=scheduled)
    _, assistant = start_baxter_chat_turn(session, thread, prompt)
    question = _question(session, question_id)
    question.last_scheduled_at = utcnow()
    question.schedule_error = ""
    session.add(question)
    session.commit()
    schedule_baxter_chat_turn(assistant.id)
    return RerunStarted(question_id=question.id, thread_id=thread.id, turn_id=assistant.id)
