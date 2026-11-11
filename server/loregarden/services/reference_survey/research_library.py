"""Queueing research picks into the library, and remembering the question they came from.

A research question puts candidates forward; a person (or the agent, gated)
queues the ones worth a survey pass. The library source then remembers the
question (`SurveySourceResearch`), so its pass brief carries the question and
the claims to confirm (`research_context`).
"""

from __future__ import annotations

from loregarden.models.domain import Workspace
from loregarden.models.domain.survey_research_tables import (
    SurveyResearchCandidate,
    SurveyResearchQuestion,
    SurveySourceResearch,
)
from loregarden.models.domain.survey_tables import SurveySource
from loregarden.services.reference_survey.bulk import BulkAddItem, enqueue_many
from loregarden.services.reference_survey.errors import SurveyError, SurveyNotFound
from loregarden.services.reference_survey.research import MAX_CANDIDATES_PER_CALL, project_url
from loregarden.services.reference_survey.schemas import BulkEntryIn, BulkSourceIn, EntryAreaIn
from pydantic import BaseModel, Field
from sqlmodel import Session, select


class QueuePicksIn(BaseModel):
    """Projects to queue for a survey pass, from research or from one project card."""

    urls: list[str] = Field(min_length=1, max_length=MAX_CANDIDATES_PER_CALL)
    #: The research question they answer; its text becomes the pass's question.
    question_id: str | None = None
    #: Required without a question; a question queues into its thread's workspace.
    workspace_slug: str = ""
    #: Project areas every pick serves, by label or id.
    areas: list[str] = Field(default_factory=list, max_length=8)


# --- Writes ---------------------------------------------------------------------


def link_source(session: Session, source_id: str, question_id: str) -> None:
    """Remember that a source came from this question (once), and point its candidates at it."""
    if session.get(SurveySourceResearch, (source_id, question_id)) is None:
        session.add(SurveySourceResearch(source_id=source_id, question_id=question_id))
    source = session.get(SurveySource, source_id)
    if source is not None:
        for candidate in session.exec(
            select(SurveyResearchCandidate).where(SurveyResearchCandidate.url == source.url)
        ).all():
            candidate.source_id = source.id
            session.add(candidate)
    session.commit()


def _question_and_workspace(
    session: Session, body: QueuePicksIn
) -> tuple[SurveyResearchQuestion | None, str]:
    if body.question_id is None:
        if not body.workspace_slug:
            raise SurveyError("Queueing without a research question needs a workspace")
        return None, body.workspace_slug
    question = session.get(SurveyResearchQuestion, body.question_id)
    if question is None:
        raise SurveyNotFound(f"No research question {body.question_id}")
    workspace = session.get(Workspace, question.workspace_id)
    if workspace is None:
        raise SurveyNotFound(f"The question's workspace {question.workspace_id} is gone")
    return question, workspace.slug


def queue_picks(session: Session, body: QueuePicksIn) -> list[BulkAddItem]:
    """Queue picks for a survey pass, admitted (a person chose them), each linked to its question.

    Each URL goes through `bulk.enqueue_many` exactly as a pasted one would, so
    a known source gains the workspace and areas and a refused one is reported
    with its reason; every URL that ends up naming a source is linked.
    """
    question, workspace_slug = _question_and_workspace(session, body)
    urls = list(dict.fromkeys(project_url(url) for url in body.urls))
    picks = [EntryAreaIn(area=area) for area in body.areas]
    items = enqueue_many(
        session,
        BulkSourceIn(
            workspace_slugs=[workspace_slug],
            urls=urls,
            question=question.question if question else "",
            queued_reason=f"Research: {question.question}" if question else "Picked from research",
            entries=[BulkEntryIn(url=url, areas=picks) for url in urls],
        ),
    )
    if question is not None:
        for item in items:
            if item.source is not None:
                link_source(session, item.source.id, question.id)
    return items


def link_enqueued(session: Session, source_id: str, question_id: str) -> None:
    """`loregarden_survey_queue` with a research question: the same link a person's queue makes."""
    if session.get(SurveyResearchQuestion, question_id) is None:
        raise SurveyNotFound(f"No research question {question_id}")
    link_source(session, source_id, question_id)
