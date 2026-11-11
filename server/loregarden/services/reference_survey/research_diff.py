"""What one run of a research question changed: the `research_diff` card's record.

A saved question is re-run so its operator learns what moved: a project that
appeared, one that is gone, a capability whose claim changed. Read from the
record, never from the agent's reply, the same way the candidates card is.

- **Added**: candidates first put forward in this run.
- **Dropped**: candidates the previous run put forward and this one did not.
  Only the latest run can say: a later run overwrites what each candidate last
  appeared in, so an older run reports ``dropped_known=False``.
- **Changed**: for candidates that were already there, each claim this run
  recorded whose status differs from the claim about the same project and
  capability that came before it (from any question or person).
"""

from __future__ import annotations

from loregarden.models.domain.survey_research_tables import (
    SurveyCapabilityClaim,
    SurveyResearchCandidate,
)
from loregarden.services.reference_survey.errors import SurveyError
from loregarden.services.reference_survey.research import (
    CandidateView,
    ClaimView,
    question_view,
)
from pydantic import BaseModel
from sqlmodel import Session, col, select


class ClaimChange(BaseModel):
    url: str
    name: str
    capability: str
    #: The claim before this run; None when this run is the first to check it.
    before: ClaimView | None
    after: ClaimView


class RunDiff(BaseModel):
    question_id: str
    question: str
    run: int
    #: The run it is compared with; 0 for a question's first run.
    previous_run: int
    #: Whether `dropped` is known: only for the question's latest run.
    dropped_known: bool
    added: list[CandidateView]
    dropped: list[CandidateView]
    changed: list[ClaimChange]
    #: Claims this run re-checked and found as they were.
    unchanged: int


def _claim_changes(
    session: Session, question_id: str, run: int, names: dict[str, str]
) -> tuple[list[ClaimChange], int]:
    """Each claim this run recorded about an existing candidate, against the one before it."""
    recorded = list(
        session.exec(
            select(SurveyCapabilityClaim).where(
                SurveyCapabilityClaim.question_id == question_id,
                SurveyCapabilityClaim.run == run,
                col(SurveyCapabilityClaim.url).in_(list(names)),
            )
        ).all()
    )
    if not recorded:
        return [], 0
    history: dict[tuple[str, str], list[SurveyCapabilityClaim]] = {}
    for claim in session.exec(
        select(SurveyCapabilityClaim)
        .where(col(SurveyCapabilityClaim.url).in_({claim.url for claim in recorded}))
        .order_by(col(SurveyCapabilityClaim.created_at))
    ).all():
        history.setdefault((claim.url, claim.capability), []).append(claim)
    changed: list[ClaimChange] = []
    unchanged = 0
    for claim in recorded:
        earlier = [
            prior
            for prior in history.get((claim.url, claim.capability), [])
            if prior.created_at < claim.created_at and prior.id != claim.id
        ]
        before = earlier[-1] if earlier else None
        if before is not None and before.status is claim.status:
            unchanged += 1
            continue
        changed.append(
            ClaimChange(
                url=claim.url,
                name=names[claim.url],
                capability=claim.capability,
                before=ClaimView.model_validate(before, from_attributes=True) if before else None,
                after=ClaimView.model_validate(claim, from_attributes=True),
            )
        )
    return changed, unchanged


def run_diff(session: Session, question_id: str, run: int | None = None) -> RunDiff:
    """What run ``run`` (default: the latest) changed against the run before it."""
    view = question_view(session, question_id)
    latest = view.run_count
    run = latest if run is None else run
    if latest == 0:
        raise SurveyError("This question has not been run yet")
    if not 1 <= run <= latest:
        raise SurveyError(f"Run {run} does not exist; this question has run {latest} times")
    rows = {
        row.url: row
        for row in session.exec(
            select(SurveyResearchCandidate).where(
                SurveyResearchCandidate.question_id == question_id
            )
        ).all()
    }
    by_url = {candidate.url: candidate for candidate in view.candidates}
    added = [by_url[url] for url, row in rows.items() if row.first_seen_run == run]
    dropped_known = run == latest
    dropped = (
        [by_url[url] for url, row in rows.items() if row.last_seen_run == run - 1]
        if dropped_known and run > 1
        else []
    )
    # Claims about a project this run first put forward are part of "added", not changes.
    existing = {url: row.name for url, row in rows.items() if row.first_seen_run < run}
    changed, unchanged = _claim_changes(session, question_id, run, existing)
    return RunDiff(
        question_id=view.id,
        question=view.question,
        run=run,
        previous_run=run - 1,
        dropped_known=dropped_known,
        added=added,
        dropped=dropped,
        changed=changed,
        unchanged=unchanged,
    )
