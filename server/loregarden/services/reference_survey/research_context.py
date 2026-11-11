"""What research knows about a project or a library source, read by the project's URL.

Claims are keyed by canonical URL, not source id, so a claim recorded before the
project was queued still belongs to its source. The pass brief and the source
page read research through here; queueing lives in `research_library`.
"""

from __future__ import annotations

from datetime import datetime

from loregarden.models.domain.survey_area_tables import SurveyArea
from loregarden.models.domain.survey_research_tables import (
    SurveyCapabilityClaim,
    SurveyResearchCandidate,
    SurveyResearchQuestion,
    SurveySourceResearch,
)
from loregarden.models.domain.survey_tables import SurveySource
from loregarden.services.reference_survey.research import (
    ClaimView,
    capabilities,
    project_url,
    shown_claim,
)
from pydantic import BaseModel
from sqlmodel import Session, col, func, select

# --- Inputs and views -----------------------------------------------------------


class QuestionRef(BaseModel):
    id: str
    question: str
    thread_id: str


class ProjectView(BaseModel):
    """Everything research knows about one project, across every question."""

    url: str
    name: str
    #: The library source for this URL, when it is in the library.
    source_id: str | None
    licence_name: str
    language: str
    stars: int | None
    last_commit_at: datetime | None
    #: The claim that stands per capability, newest first; operators' refutations win.
    claims: list[ClaimView]
    #: The questions that put it forward, newest first.
    questions: list[QuestionRef]


class QuestionSummary(BaseModel):
    """One research question in a workspace, for the library's list of them."""

    id: str
    question: str
    thread_id: str
    capabilities: list[str]
    run_count: int
    last_run_at: datetime | None
    candidates: int
    #: Saved questions list first and can re-run on their own.
    saved: bool
    #: Days between automatic re-runs; None re-runs only when asked.
    interval_days: int | None
    #: The project area it is pinned to, by name; "" when none.
    area_name: str
    #: Why the last scheduled re-run could not start; "" when it did.
    schedule_error: str


class ResearchContext(BaseModel):
    """A question a library source came from, and what research claimed about it."""

    question_id: str
    question: str
    capabilities: list[str]
    claims: list[ClaimView]


# --- Reads ----------------------------------------------------------------------


def _standing_by_capability(session: Session, url: str) -> dict[str, list[SurveyCapabilityClaim]]:
    grouped: dict[str, list[SurveyCapabilityClaim]] = {}
    for claim in session.exec(
        select(SurveyCapabilityClaim).where(
            SurveyCapabilityClaim.url == url, col(SurveyCapabilityClaim.superseded_by).is_(None)
        )
    ).all():
        grouped.setdefault(claim.capability, []).append(claim)
    return grouped


def _shown_claims(session: Session, url: str) -> list[ClaimView]:
    shown = [
        claim
        for claims in _standing_by_capability(session, url).values()
        if (claim := shown_claim(claims)) is not None
    ]
    shown.sort(key=lambda claim: claim.created_at, reverse=True)
    return [ClaimView.model_validate(claim, from_attributes=True) for claim in shown]


def project_view(session: Session, raw_url: str) -> ProjectView:
    """What research knows about a project; empty claims when it was never researched."""
    url = project_url(raw_url)
    rows = session.exec(
        select(SurveyResearchCandidate, SurveyResearchQuestion)
        .join(
            SurveyResearchQuestion,
            col(SurveyResearchQuestion.id) == SurveyResearchCandidate.question_id,
        )
        .where(SurveyResearchCandidate.url == url)
        .order_by(col(SurveyResearchCandidate.updated_at).desc())
    ).all()
    latest = rows[0][0] if rows else None
    source_id = session.exec(select(SurveySource.id).where(SurveySource.url == url)).first()
    return ProjectView(
        url=url,
        name=latest.name if latest else "",
        source_id=source_id,
        licence_name=latest.licence_name if latest else "",
        language=latest.language if latest else "",
        stars=latest.stars if latest else None,
        last_commit_at=latest.last_commit_at if latest else None,
        claims=_shown_claims(session, url),
        questions=[
            QuestionRef(id=question.id, question=question.question, thread_id=question.thread_id)
            for _candidate, question in rows
        ],
    )


def source_research(session: Session, source: SurveySource) -> list[ResearchContext]:
    """The questions this source was queued from, each with the claims to confirm."""
    questions = session.exec(
        select(SurveyResearchQuestion)
        .join(
            SurveySourceResearch, col(SurveySourceResearch.question_id) == SurveyResearchQuestion.id
        )
        .where(SurveySourceResearch.source_id == source.id)
        .order_by(col(SurveySourceResearch.added_at))
    ).all()
    if not questions:
        return []
    claims = _shown_claims(session, source.url)
    contexts = []
    for question in questions:
        wanted = capabilities(question)
        contexts.append(
            ResearchContext(
                question_id=question.id,
                question=question.question,
                capabilities=wanted,
                claims=[claim for claim in claims if claim.capability in wanted],
            )
        )
    return contexts


def brief_lines(contexts: list[ResearchContext]) -> list[str]:
    """The pass brief's research lines: each question, and each claim to confirm or refute."""
    lines = []
    for context in contexts:
        claimed = {claim.capability: claim for claim in context.claims}
        parts = []
        for capability in context.capabilities:
            claim = claimed.get(capability)
            if claim is None:
                parts.append(f"{capability} (not checked)")
            elif claim.evidence_path:
                parts.append(
                    f"{capability} ({claim.status.value}: {claim.evidence_path}"
                    f"@{claim.evidence_commit[:12]})"
                )
            else:
                parts.append(f"{capability} ({claim.status.value})")
        lines.append(
            f"**Research question:** {context.question} — confirm or refute each claim with "
            f"`loregarden_research_record`: {'; '.join(parts)}"
        )
    return lines


#: The library's research list shows the newest this many questions.
MAX_LISTED_QUESTIONS = 50


def workspace_questions(session: Session, workspace_id: str) -> list[QuestionSummary]:
    """A workspace's research questions, most recently run first, with their candidate counts."""
    questions = session.exec(
        select(SurveyResearchQuestion)
        .where(
            SurveyResearchQuestion.workspace_id == workspace_id,
            col(SurveyResearchQuestion.archived_at).is_(None),
        )
        .order_by(
            col(SurveyResearchQuestion.saved).desc(),
            col(SurveyResearchQuestion.last_run_at).desc(),
            col(SurveyResearchQuestion.created_at).desc(),
        )
        .limit(MAX_LISTED_QUESTIONS)
    ).all()
    counts: dict[str, int] = dict(
        session.exec(
            select(SurveyResearchCandidate.question_id, func.count())
            .where(col(SurveyResearchCandidate.question_id).in_([q.id for q in questions]))
            .group_by(col(SurveyResearchCandidate.question_id))
        ).all()
    )
    area_ids = {question.area_id for question in questions if question.area_id}
    areas: dict[str, str] = (
        dict(
            session.exec(
                select(SurveyArea.id, SurveyArea.name).where(col(SurveyArea.id).in_(area_ids))
            ).all()
        )
        if area_ids
        else {}
    )
    return [
        QuestionSummary(
            id=question.id,
            question=question.question,
            thread_id=question.thread_id,
            capabilities=capabilities(question),
            run_count=question.run_count,
            last_run_at=question.last_run_at,
            candidates=counts.get(question.id, 0),
            saved=question.saved,
            interval_days=question.interval_days,
            area_name=areas.get(question.area_id or "", ""),
            schedule_error=question.schedule_error,
        )
        for question in questions
    ]
