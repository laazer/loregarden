"""Research questions and their candidates, for the research card and the library.

The card a research turn emits carries only a question id; it reads the table
from here, so what a person sees is what was recorded, never what the agent
typed. A person's correction is recorded here too.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from loregarden.db.session import get_session
from loregarden.services.baxter_chat_service import BaxterChatConflictError
from loregarden.services.reference_survey.bulk import BulkAddItem
from loregarden.services.reference_survey.errors import SurveyError, SurveyNotFound
from loregarden.services.reference_survey.report import workspace_id_for
from loregarden.services.reference_survey.research import (
    ClaimCard,
    ClaimView,
    QuestionView,
    RefuteIn,
    claim_card,
    question_view,
    refute,
)
from loregarden.services.reference_survey.research_context import (
    ProjectView,
    QuestionSummary,
    project_view,
    workspace_questions,
)
from loregarden.services.reference_survey.research_diff import RunDiff, run_diff
from loregarden.services.reference_survey.research_library import QueuePicksIn, queue_picks
from loregarden.services.reference_survey.research_saved import (
    RerunStarted,
    SaveQuestionIn,
    rerun_question,
    save_question,
)
from sqlmodel import Session

router = APIRouter(prefix="/reference-research", tags=["reference-library"])


@router.get("/questions", response_model=list[QuestionSummary])
def research_questions(
    workspace: str, session: Session = Depends(get_session)
) -> list[QuestionSummary]:
    """A workspace's research questions, most recently run first."""
    try:
        return workspace_questions(session, workspace_id_for(session, workspace))
    except SurveyNotFound as exc:
        raise HTTPException(404, str(exc)) from exc


@router.get("/projects", response_model=ProjectView)
def research_project(url: str, session: Session = Depends(get_session)) -> ProjectView:
    """Everything research knows about one project, across every question."""
    try:
        return project_view(session, url)
    except SurveyError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/queue", response_model=list[BulkAddItem])
def queue_research_picks(
    body: QueuePicksIn, session: Session = Depends(get_session)
) -> list[BulkAddItem]:
    """Queue picks for a survey pass, admitted, each linked to the question it answers."""
    try:
        return queue_picks(session, body)
    except SurveyNotFound as exc:
        raise HTTPException(404, str(exc)) from exc
    except SurveyError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/questions/{question_id}", response_model=QuestionView)
def research_question(question_id: str, session: Session = Depends(get_session)) -> QuestionView:
    """A question, its candidates, and the claim that stands per capability."""
    try:
        return question_view(session, question_id)
    except SurveyNotFound as exc:
        raise HTTPException(404, str(exc)) from exc


@router.post("/questions/{question_id}/save", response_model=QuestionView)
def save_research_question(
    question_id: str, body: SaveQuestionIn, session: Session = Depends(get_session)
) -> QuestionView:
    """Save, re-pin or un-save a question; an interval makes it re-run on its own."""
    try:
        return save_question(session, question_id, body)
    except SurveyNotFound as exc:
        raise HTTPException(404, str(exc)) from exc
    except SurveyError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/questions/{question_id}/rerun", response_model=RerunStarted)
def rerun_research_question(
    question_id: str, session: Session = Depends(get_session)
) -> RerunStarted:
    """Re-run a question now, as a turn in its thread. 409 while that thread is busy."""
    try:
        return rerun_question(session, question_id)
    except BaxterChatConflictError as exc:
        raise HTTPException(409, str(exc)) from exc
    except SurveyNotFound as exc:
        raise HTTPException(404, str(exc)) from exc
    except SurveyError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/questions/{question_id}/diff", response_model=RunDiff)
def research_run_diff(
    question_id: str, run: int | None = None, session: Session = Depends(get_session)
) -> RunDiff:
    """What one run (default: the latest) changed against the run before it."""
    try:
        return run_diff(session, question_id, run)
    except SurveyNotFound as exc:
        raise HTTPException(404, str(exc)) from exc
    except SurveyError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/claims", response_model=ClaimCard)
def research_claim(url: str, capability: str, session: Session = Depends(get_session)) -> ClaimCard:
    """The claim that stands for one project and capability, for the claim card."""
    try:
        return claim_card(session, url, capability)
    except SurveyError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/claims/refute", response_model=ClaimView)
def refute_claim(body: RefuteIn, session: Session = Depends(get_session)) -> ClaimView:
    """A person's correction: this project does not do this. It stands over agent claims."""
    try:
        return ClaimView.model_validate(refute(session, body), from_attributes=True)
    except SurveyNotFound as exc:
        raise HTTPException(404, str(exc)) from exc
    except SurveyError as exc:
        raise HTTPException(400, str(exc)) from exc
