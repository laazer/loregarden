"""Research questions, candidates and capability claims: the record behind a research thread.

A research thread asks a capability question, puts candidate projects forward,
and claims for each one how far each capability was checked. The rules that
make the record worth reading live here, not in the prompt:

- A ``verified`` claim must name a file at the commit research read and the
  lines in it that do the thing (`research_repo.verify_evidence`); otherwise it
  is refused, with the reason, and nothing about it is stored. The lines and the
  link are read by the server, not taken from the agent.
- A person's refutation stands. A later agent claim about the same project and
  capability is stored beside it, not over it, and the reply says so.
- A claim is about one of the question's capabilities, so the comparison table
  has a column for every claim it shows.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime

from loregarden.models.domain import BaxterChatSession, SurveySource
from loregarden.models.domain.chat_enums import ChatThreadMode
from loregarden.models.domain.enums import utcnow
from loregarden.models.domain.survey_enums import ClaimRecorder, ClaimStatus, SurveySourceKind
from loregarden.models.domain.survey_research_tables import (
    SurveyCapabilityClaim,
    SurveyResearchCandidate,
    SurveyResearchQuestion,
)
from loregarden.services.reference_repo_service import parse_repo_url
from loregarden.services.reference_survey.errors import SurveyError, SurveyNotFound
from loregarden.services.reference_survey.queue import canonical_url
from loregarden.services.reference_survey.research_repo import Evidence, verify_evidence
from loregarden.services.reference_survey.source_kind import infer_kind
from loregarden.services.reference_survey.upstream import (
    UpstreamUnavailable,
    gh_api_json,
    parse_github,
)
from pydantic import BaseModel, Field, field_validator
from sqlmodel import Session, col, select

logger = logging.getLogger(__name__)

MAX_CAPABILITIES = 8
MAX_CANDIDATES_PER_CALL = 15
#: How many of a person's refutations a research prompt carries.
MAX_PROMPT_REFUTATIONS = 40


# --- Inputs -------------------------------------------------------------------


class QuestionIn(BaseModel):
    thread_id: str
    question: str = Field(min_length=8, max_length=600)
    capabilities: list[str] = Field(min_length=1, max_length=MAX_CAPABILITIES)

    @field_validator("capabilities")
    @classmethod
    def _distinct(cls, value: list[str]) -> list[str]:
        seen: list[str] = []
        for item in value:
            text = item.strip()
            if not text:
                raise ValueError("a capability cannot be blank")
            if text in seen:
                raise ValueError(f"capability listed twice: {text}")
            seen.append(text)
        return seen


class ClaimIn(BaseModel):
    capability: str
    status: ClaimStatus
    evidence_path: str = ""
    evidence_commit: str = ""
    evidence_start: int = 0
    evidence_end: int = 0
    evidence_url: str = ""
    note: str = Field(default="", max_length=600)


class CandidateIn(BaseModel):
    url: str
    name: str = Field(min_length=1, max_length=120)
    summary: str = Field(default="", max_length=400)
    claims: list[ClaimIn] = Field(default_factory=list)


class RefuteIn(BaseModel):
    url: str
    capability: str = Field(min_length=3, max_length=300)
    note: str = Field(min_length=3, max_length=600)
    question_id: str | None = None


# --- Views --------------------------------------------------------------------


class ClaimView(BaseModel):
    id: str
    capability: str
    status: ClaimStatus
    evidence_path: str
    evidence_commit: str
    evidence_start: int
    evidence_end: int
    evidence_excerpt: str
    evidence_url: str
    note: str
    recorded_by: ClaimRecorder
    created_at: datetime


class ClaimCard(BaseModel):
    """One project and one capability: the claim that stands, for the claim card."""

    url: str
    capability: str
    #: The name research put the project forward under, or "" if never a candidate.
    name: str
    claim: ClaimView | None
    #: Other claims recorded about this project and capability, replaced or outranked.
    earlier: int


class CandidateView(BaseModel):
    id: str
    url: str
    name: str
    summary: str
    source_id: str | None
    licence_name: str
    language: str
    stars: int | None
    last_commit_at: datetime | None
    metadata_error: str
    #: First put forward in the question's latest run, after an earlier one.
    new: bool
    #: Capability -> the claim that stands, or None when nothing was claimed.
    claims: dict[str, ClaimView | None]


class QuestionView(BaseModel):
    id: str
    thread_id: str
    question: str
    capabilities: list[str]
    run_count: int
    last_run_at: datetime | None
    saved: bool
    candidates: list[CandidateView]


class RecordResult(BaseModel):
    question_id: str
    run: int
    recorded: list[str]
    #: "<url> · <capability>: <why>" for each claim that was not stored.
    refused: list[str]
    warnings: list[str]


# --- Writes -------------------------------------------------------------------


def _research_thread(session: Session, thread_id: str) -> BaxterChatSession:
    thread = session.get(BaxterChatSession, thread_id)
    if thread is None:
        raise SurveyNotFound(f"No chat thread {thread_id}")
    if thread.mode is not ChatThreadMode.RESEARCH:
        raise SurveyError("Research questions belong to a research thread; this one is general")
    return thread


def open_question(session: Session, body: QuestionIn) -> SurveyResearchQuestion:
    thread = _research_thread(session, body.thread_id)
    question = SurveyResearchQuestion(
        workspace_id=thread.workspace_id,
        thread_id=thread.id,
        question=body.question.strip(),
        capabilities_json=json.dumps(body.capabilities),
    )
    session.add(question)
    session.commit()
    session.refresh(question)
    return question


def _question(session: Session, question_id: str) -> SurveyResearchQuestion:
    question = session.get(SurveyResearchQuestion, question_id)
    if question is None:
        raise SurveyNotFound(f"No research question {question_id}")
    return question


def capabilities(question: SurveyResearchQuestion) -> list[str]:
    return list(json.loads(question.capabilities_json or "[]"))


def project_url(raw: str) -> str:
    """The one spelling a project is recorded under, the same one the library uses."""
    return canonical_url(infer_kind(raw), raw)


class _Licence(BaseModel):
    spdx_id: str | None = None
    name: str | None = None


class _RepoAbout(BaseModel):
    language: str | None = None
    stargazers_count: int = 0
    pushed_at: datetime | None = None
    license: _Licence | None = None


def _fill_metadata(candidate: SurveyResearchCandidate) -> str:
    """Read a GitHub repository's facts onto the candidate; the reason when it cannot."""
    if infer_kind(candidate.url) is not SurveySourceKind.REPO:
        return ""  # a site has no repository facts to read; nothing failed
    parsed = parse_repo_url(candidate.url)
    if parsed.host != "github.com":
        return ""
    try:
        about = parse_github(_RepoAbout, gh_api_json(f"repos/{parsed.owner}/{parsed.name}"))
    except UpstreamUnavailable as exc:
        logger.warning("Research metadata for %s could not be read: %s", candidate.url, exc)
        candidate.metadata_error = str(exc)
        return f"{candidate.url}: facts not read ({exc})"
    licence = about.license
    candidate.licence_name = (licence.spdx_id or licence.name or "") if licence else ""
    candidate.language = about.language or ""
    candidate.stars = about.stargazers_count
    candidate.last_commit_at = about.pushed_at
    candidate.metadata_error = ""
    return ""


def _standing(session: Session, url: str, capability: str) -> list[SurveyCapabilityClaim]:
    return list(
        session.exec(
            select(SurveyCapabilityClaim).where(
                SurveyCapabilityClaim.url == url,
                SurveyCapabilityClaim.capability == capability,
                col(SurveyCapabilityClaim.superseded_by).is_(None),
            )
        ).all()
    )


def _person_refuted(claims: list[SurveyCapabilityClaim]) -> SurveyCapabilityClaim | None:
    for claim in claims:
        if claim.recorded_by is ClaimRecorder.OPERATOR and claim.status is ClaimStatus.REFUTED:
            return claim
    return None


def _store_claim(session: Session, claim: SurveyCapabilityClaim) -> str:
    """Store a claim over the one standing; the warning when a person's refutation stands."""
    standing = _standing(session, claim.url, claim.capability)
    refuted = _person_refuted(standing)
    session.add(claim)
    session.flush()
    if refuted is not None and claim.recorded_by is ClaimRecorder.AGENT:
        return (
            f"{claim.url} · {claim.capability}: recorded, but a person refuted this on "
            f"{refuted.created_at:%Y-%m-%d} ({refuted.note}); the refutation stands"
        )
    for old in standing:
        old.superseded_by = claim.id
        session.add(old)
    return ""


def _source_id(session: Session, url: str) -> str | None:
    return session.exec(select(SurveySource.id).where(SurveySource.url == url)).first()


def _upsert_candidate(
    session: Session, question: SurveyResearchQuestion, url: str, body: CandidateIn, run: int
) -> SurveyResearchCandidate:
    candidate = session.exec(
        select(SurveyResearchCandidate).where(
            SurveyResearchCandidate.question_id == question.id,
            SurveyResearchCandidate.url == url,
        )
    ).first()
    if candidate is None:
        candidate = SurveyResearchCandidate(
            question_id=question.id, url=url, name=body.name, first_seen_run=run
        )
    candidate.name = body.name.strip()
    candidate.summary = body.summary.strip()
    candidate.last_seen_run = run
    candidate.source_id = _source_id(session, url)
    candidate.updated_at = utcnow()
    return candidate


def _cites_source(claim: ClaimIn) -> bool:
    """Verified always cites source lines; a refutation may, and is then held to the same check."""
    return claim.status is ClaimStatus.VERIFIED or (
        claim.status is ClaimStatus.REFUTED and claim.evidence_start > 0
    )


def _check_claim(claim: ClaimIn, url: str, wanted: list[str]) -> Evidence:
    if claim.capability not in wanted:
        return Evidence(refusal="not one of the question's capabilities: " + "; ".join(wanted))
    if _cites_source(claim):
        return verify_evidence(
            url,
            claim.evidence_commit.strip(),
            claim.evidence_path.strip(),
            claim.evidence_start,
            claim.evidence_end,
        )
    return Evidence()


def _agent_claim(
    claim: ClaimIn, evidence: Evidence, url: str, candidate: SurveyResearchCandidate, run_id: str
) -> SurveyCapabilityClaim:
    cited = _cites_source(claim)
    return SurveyCapabilityClaim(
        url=url,
        source_id=candidate.source_id,
        capability=claim.capability,
        status=claim.status,
        evidence_path=claim.evidence_path.strip(),
        evidence_commit=claim.evidence_commit.strip(),
        evidence_start=claim.evidence_start if cited else 0,
        evidence_end=claim.evidence_end if cited else 0,
        evidence_excerpt=evidence.excerpt,
        evidence_url=evidence.url or claim.evidence_url.strip(),
        note=claim.note.strip(),
        recorded_by=ClaimRecorder.AGENT,
        run_id=run_id,
        question_id=candidate.question_id,
    )


def record_candidates(
    session: Session, question_id: str, candidates: list[CandidateIn], *, run_id: str = ""
) -> RecordResult:
    """One research run's candidates and claims; refused claims are named, never dropped."""
    if not candidates:
        raise SurveyError("Record at least one candidate")
    if len(candidates) > MAX_CANDIDATES_PER_CALL:
        raise SurveyError(f"At most {MAX_CANDIDATES_PER_CALL} candidates per call")
    question = _question(session, question_id)
    wanted = capabilities(question)
    question.run_count += 1
    question.last_run_at = utcnow()
    question.updated_at = question.last_run_at
    run = question.run_count
    result = RecordResult(question_id=question.id, run=run, recorded=[], refused=[], warnings=[])
    for body in candidates:
        try:
            url = project_url(body.url)
        except SurveyError as exc:
            result.refused.append(f"{body.url}: {exc}")
            continue
        candidate = _upsert_candidate(session, question, url, body, run)
        if warning := _fill_metadata(candidate):
            result.warnings.append(warning)
        session.add(candidate)
        result.recorded.append(url)
        claimed: set[str] = set()
        for claim in body.claims:
            evidence = _check_claim(claim, url, wanted)
            if evidence.refusal:
                result.refused.append(f"{url} · {claim.capability}: {evidence.refusal}")
                continue
            claimed.add(claim.capability)
            stored = _agent_claim(claim, evidence, url, candidate, run_id)
            if warning := _store_claim(session, stored):
                result.warnings.append(warning)
        if missing := [cap for cap in wanted if cap not in claimed]:
            result.warnings.append(f"{url}: nothing claimed for " + "; ".join(missing))
    session.add(question)
    session.commit()
    return result


def refute(session: Session, body: RefuteIn) -> SurveyCapabilityClaim:
    """A person's correction: this project does not do this. It stands over agent claims."""
    url = project_url(body.url)
    if body.question_id is not None:
        _question(session, body.question_id)
    claim = SurveyCapabilityClaim(
        url=url,
        source_id=_source_id(session, url),
        capability=body.capability.strip(),
        status=ClaimStatus.REFUTED,
        note=body.note.strip(),
        recorded_by=ClaimRecorder.OPERATOR,
        question_id=body.question_id,
    )
    _store_claim(session, claim)
    session.commit()
    session.refresh(claim)
    return claim


# --- Reads --------------------------------------------------------------------


def _claim_view(claim: SurveyCapabilityClaim) -> ClaimView:
    return ClaimView.model_validate(claim, from_attributes=True)


def shown_claim(claims: list[SurveyCapabilityClaim]) -> SurveyCapabilityClaim | None:
    """A person's refutation if one stands, else the newest standing claim."""
    if refuted := _person_refuted(claims):
        return refuted
    return max(claims, key=lambda claim: claim.created_at, default=None)


def question_view(session: Session, question_id: str) -> QuestionView:
    question = _question(session, question_id)
    wanted = capabilities(question)
    candidates = list(
        session.exec(
            select(SurveyResearchCandidate)
            .where(SurveyResearchCandidate.question_id == question.id)
            .order_by(col(SurveyResearchCandidate.created_at))
        ).all()
    )
    urls = [candidate.url for candidate in candidates]
    standing: dict[tuple[str, str], list[SurveyCapabilityClaim]] = {}
    for claim in session.exec(
        select(SurveyCapabilityClaim).where(
            col(SurveyCapabilityClaim.url).in_(urls),
            col(SurveyCapabilityClaim.capability).in_(wanted),
            col(SurveyCapabilityClaim.superseded_by).is_(None),
        )
    ).all():
        standing.setdefault((claim.url, claim.capability), []).append(claim)
    views = []
    for candidate in candidates:
        shown = {cap: shown_claim(standing.get((candidate.url, cap), [])) for cap in wanted}
        views.append(
            CandidateView(
                id=candidate.id,
                url=candidate.url,
                name=candidate.name,
                summary=candidate.summary,
                source_id=candidate.source_id,
                licence_name=candidate.licence_name,
                language=candidate.language,
                stars=candidate.stars,
                last_commit_at=candidate.last_commit_at,
                metadata_error=candidate.metadata_error,
                new=question.run_count > 1 and candidate.first_seen_run == question.run_count,
                claims={cap: _claim_view(c) if c else None for cap, c in shown.items()},
            )
        )
    return QuestionView(
        id=question.id,
        thread_id=question.thread_id,
        question=question.question,
        capabilities=wanted,
        run_count=question.run_count,
        last_run_at=question.last_run_at,
        saved=question.saved,
        candidates=views,
    )


def claim_card(session: Session, raw_url: str, capability: str) -> ClaimCard:
    """The claim that stands for one project and capability, and how many came before it."""
    url = project_url(raw_url)
    claims = list(
        session.exec(
            select(SurveyCapabilityClaim).where(
                SurveyCapabilityClaim.url == url, SurveyCapabilityClaim.capability == capability
            )
        ).all()
    )
    shown = shown_claim([claim for claim in claims if claim.superseded_by is None])
    name = session.exec(
        select(SurveyResearchCandidate.name)
        .where(SurveyResearchCandidate.url == url)
        .order_by(col(SurveyResearchCandidate.updated_at).desc())
    ).first()
    return ClaimCard(
        url=url,
        capability=capability,
        name=name or "",
        claim=_claim_view(shown) if shown else None,
        earlier=len(claims) - (1 if shown else 0),
    )


def thread_questions(session: Session, thread_id: str) -> list[SurveyResearchQuestion]:
    return list(
        session.exec(
            select(SurveyResearchQuestion)
            .where(SurveyResearchQuestion.thread_id == thread_id)
            .order_by(col(SurveyResearchQuestion.created_at))
        ).all()
    )


def person_refutations(session: Session) -> list[SurveyCapabilityClaim]:
    """The newest standing refutations people made, for a research prompt to respect."""
    return list(
        session.exec(
            select(SurveyCapabilityClaim)
            .where(
                SurveyCapabilityClaim.recorded_by == ClaimRecorder.OPERATOR,
                SurveyCapabilityClaim.status == ClaimStatus.REFUTED,
                col(SurveyCapabilityClaim.superseded_by).is_(None),
            )
            .order_by(col(SurveyCapabilityClaim.created_at).desc())
            .limit(MAX_PROMPT_REFUTATIONS)
        ).all()
    )
