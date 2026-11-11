"""Research questions and their candidates, for the research card and the library.

The card a research turn emits carries only a question id; it reads the table
from here, so what a person sees is what was recorded, never what the agent
typed. A person's correction is recorded here too.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from loregarden.db.session import get_session
from loregarden.services.reference_survey.errors import SurveyError, SurveyNotFound
from loregarden.services.reference_survey.research import (
    ClaimCard,
    ClaimView,
    QuestionView,
    RefuteIn,
    claim_card,
    question_view,
    refute,
)
from sqlmodel import Session

router = APIRouter(prefix="/reference-research", tags=["reference-library"])


@router.get("/questions/{question_id}", response_model=QuestionView)
def research_question(question_id: str, session: Session = Depends(get_session)) -> QuestionView:
    """A question, its candidates, and the claim that stands per capability."""
    try:
        return question_view(session, question_id)
    except SurveyNotFound as exc:
        raise HTTPException(404, str(exc)) from exc


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
