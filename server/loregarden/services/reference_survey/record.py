"""Evidence, findings, retractions and unknowns: the survey's epistemic record.

Adapted from morluto/rea @166fb08 (MIT), `.agents/skills/reverse-engineer-anything/
references/evidence-workflows.md`, reimplemented. Its tooling is out of scope; its
discipline is the transfer:

- Every claim cites evidence by id, and the evidence carries its authority,
  limitations, corpus and residual unknowns.
- Corpus completeness is kept apart from page exhaustion: "a missing match in
  partial source Evidence does not establish absence." So an ABSENCE finding is
  refused unless every piece of evidence it cites searched a corpus recorded as
  complete. Twice in one day the survey filed absence claimed from an incomplete
  corpus (visual-QA "missing" because its runner sat outside `client/src`; nav
  links "unnamed" because an accessibility read missed a clipped label), and both
  reached tickets before they were retracted.
- An unknown names the authority still required to settle it, keeps supporting
  and contradicting evidence side by side, is resolved only by observed evidence,
  and is updated against its current revision.
"""

from __future__ import annotations

import json
from typing import TypeVar

from loregarden.models.domain.enums import utcnow
from loregarden.models.domain.survey_enums import (
    SurveyEpistemic,
    SurveyEvidenceStance,
    SurveyFindingClaim,
    SurveyFindingStatus,
    SurveyUnknownStatus,
)
from loregarden.models.domain.survey_tables import (
    SurveyEvidence,
    SurveyFinding,
    SurveyFindingEvidence,
    SurveyPass,
    SurveySource,
    SurveyUnknown,
    SurveyUnknownEvidence,
)
from loregarden.services.reference_survey.errors import (
    StaleRevision,
    SurveyError,
    SurveyNotFound,
)
from loregarden.services.reference_survey.schemas import (
    EvidenceIn,
    FindingIn,
    RetractIn,
    UnknownOpenIn,
    UnknownUpdateIn,
)
from sqlalchemy import update
from sqlmodel import Session, col, select

T = TypeVar("T")


def _get(session: Session, model: type[T], row_id: str | None, what: str) -> T:
    row = session.get(model, row_id) if row_id else None
    if row is None:
        raise SurveyNotFound(f"No {what} {row_id!r}")
    return row


def _check_pass(session: Session, source_id: str, pass_id: str | None) -> None:
    _get(session, SurveySource, source_id, "survey source")
    if (
        pass_id is not None
        and _get(session, SurveyPass, pass_id, "survey pass").source_id != source_id
    ):
        raise SurveyError(f"Pass {pass_id} belongs to a different source")


def record_evidence(session: Session, body: EvidenceIn) -> SurveyEvidence:
    _check_pass(session, body.source_id, body.pass_id)
    row = SurveyEvidence(
        source_id=body.source_id,
        pass_id=body.pass_id,
        epistemic=body.epistemic,
        operation=body.operation,
        result=body.result,
        authority=body.authority,
        limitations=body.limitations,
        residual_unknowns=body.residual_unknowns,
        corpus_json=json.dumps(body.corpus),
        corpus_complete=body.corpus_complete,
        completeness_basis=body.completeness_basis,
        examined_count=body.examined_count,
        total_count=body.total_count,
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


def _evidence(session: Session, ids: list[str], source_id: str) -> list[SurveyEvidence]:
    rows = [_get(session, SurveyEvidence, i, "evidence") for i in dict.fromkeys(ids)]
    foreign = [r.id for r in rows if r.source_id != source_id]
    if foreign:
        raise SurveyError(f"Evidence {', '.join(foreign)} is about a different source")
    return rows


def _claim_problem(body: FindingIn, rows: list[SurveyEvidence]) -> str:
    """Why this claim is not supported by what it cites, or "" when it is."""
    observed = [r for r in rows if r.epistemic is SurveyEpistemic.OBSERVED]
    if body.epistemic is SurveyEpistemic.OBSERVED and len(observed) != len(rows):
        return (
            "A finding marked observed may cite only observed evidence; "
            f"{len(rows) - len(observed)} of {len(rows)} cited records are inferences. "
            "Mark the finding inferred, or cite what was observed."
        )
    if body.claim is SurveyFindingClaim.ABSENCE:
        incomplete = [r.id for r in rows if not r.corpus_complete]
        if incomplete or len(observed) != len(rows):
            return (
                "An absence needs observed evidence whose corpus is recorded as complete for "
                f"the question; {', '.join(incomplete) or 'some cited evidence'} is not. "
                "A missing match in a partial corpus does not establish absence: record this "
                "as not_found, which names the corpus searched, or search a corpus you can "
                "show is complete."
            )
    if body.claim is SurveyFindingClaim.NOT_FOUND and not any(
        json.loads(r.corpus_json) for r in rows
    ):
        return "not_found must cite evidence that names the corpus it searched."
    return ""


def record_finding(session: Session, body: FindingIn) -> SurveyFinding:
    _check_pass(session, body.source_id, body.pass_id)
    rows = _evidence(session, body.evidence_ids, body.source_id)
    problem = _claim_problem(body, rows)
    if problem:
        raise SurveyError(problem)
    finding = SurveyFinding(
        source_id=body.source_id,
        pass_id=body.pass_id,
        claim=body.claim,
        epistemic=body.epistemic,
        statement=body.statement,
    )
    session.add(finding)
    session.flush()
    for row in rows:
        session.add(SurveyFindingEvidence(finding_id=finding.id, evidence_id=row.id))
    session.commit()
    session.refresh(finding)
    return finding


def finding_evidence(session: Session, finding_id: str) -> list[SurveyEvidence]:
    return list(
        session.exec(
            select(SurveyEvidence)
            .join(
                SurveyFindingEvidence, col(SurveyFindingEvidence.evidence_id) == SurveyEvidence.id
            )
            .where(SurveyFindingEvidence.finding_id == finding_id)
            .order_by(col(SurveyEvidence.created_at))
        )
    )


def _corpus_phrase(rows: list[SurveyEvidence]) -> str:
    paths = list(dict.fromkeys(p for r in rows for p in json.loads(r.corpus_json)))
    shown = ", ".join(f"`{p}`" for p in paths[:6]) + (
        f" and {len(paths) - 6} more" if len(paths) > 6 else ""
    )
    counted = [r for r in rows if r.total_count is not None]
    if counted:
        examined = sum(r.examined_count or 0 for r in counted)
        total = sum(r.total_count or 0 for r in counted)
        shown += f" ({examined} of {total} examined)"
    return shown or "an unnamed corpus"


def render_finding(finding: SurveyFinding, rows: list[SurveyEvidence]) -> str:
    """One line a ticket reader can tell apart: observed, inferred, absent, not found.

    The three claim kinds never render the same, and an inference says so in the
    words a reader sees, not only in a field nobody opens.
    """
    cites = ", ".join(f"`{r.id}`" for r in rows)
    if finding.claim is SurveyFindingClaim.ABSENCE:
        lead = f"**Absent** (searched a complete corpus: {_corpus_phrase(rows)})"
    elif finding.claim is SurveyFindingClaim.NOT_FOUND:
        lead = (
            f"**Not found** in {_corpus_phrase(rows)}. That corpus is not shown to be "
            "complete, so this does not establish absence"
        )
    elif finding.epistemic is SurveyEpistemic.OBSERVED:
        lead = "**Observed**"
    else:
        lead = "**Inferred, not observed**"
    text = f"{lead}: {finding.statement} (evidence {cites})"
    if finding.status is SurveyFindingStatus.RETRACTED:
        text = f"~~{text}~~ **Retracted:** {finding.retracted_reason}"
    return text


def retract_finding(session: Session, body: RetractIn) -> SurveyFinding:
    finding = _get(session, SurveyFinding, body.finding_id, "finding")
    if finding.status is SurveyFindingStatus.RETRACTED:
        raise SurveyError(f"Finding {finding.id} is already retracted: {finding.retracted_reason}")
    if body.replaced_by_id is not None:
        replacement = _get(session, SurveyFinding, body.replaced_by_id, "finding")
        if replacement.id == finding.id or replacement.status is SurveyFindingStatus.RETRACTED:
            raise SurveyError("A retraction is replaced by a different, active finding")
    finding.status = SurveyFindingStatus.RETRACTED
    finding.retracted_reason = body.reason
    finding.replaced_by_id = body.replaced_by_id
    finding.retracted_at = utcnow()
    session.add(finding)
    session.commit()
    session.refresh(finding)
    return finding


def _link(
    session: Session, unknown_id: str, ids: list[str], stance: SurveyEvidenceStance, source_id: str
) -> None:
    for row in _evidence(session, ids, source_id):
        existing = session.get(SurveyUnknownEvidence, (unknown_id, row.id))
        if existing is None:
            session.add(
                SurveyUnknownEvidence(unknown_id=unknown_id, evidence_id=row.id, stance=stance)
            )
        elif existing.stance is not stance:
            raise SurveyError(
                f"Evidence {row.id} is already recorded as {existing.stance.value} for this "
                "unknown. One record bears on a question one way; record a second observation."
            )


def open_unknown(session: Session, body: UnknownOpenIn) -> SurveyUnknown:
    _check_pass(session, body.source_id, body.pass_id)
    unknown = SurveyUnknown(
        source_id=body.source_id,
        opened_by_pass_id=body.pass_id,
        question=body.question,
        authority_required=body.authority_required,
    )
    session.add(unknown)
    session.flush()
    _link(session, unknown.id, body.supporting, SurveyEvidenceStance.SUPPORTING, body.source_id)
    _link(
        session, unknown.id, body.contradicting, SurveyEvidenceStance.CONTRADICTING, body.source_id
    )
    session.commit()
    session.refresh(unknown)
    return unknown


def update_unknown(session: Session, body: UnknownUpdateIn) -> SurveyUnknown:
    """Add evidence to an unknown or resolve it, against the revision the caller read.

    The revision is claimed with a compare-and-set before anything else is
    written, so two passes updating the same unknown cannot both succeed: the
    second gets `StaleRevision` and rereads.
    """
    unknown = _get(session, SurveyUnknown, body.unknown_id, "unknown")
    if unknown.status is SurveyUnknownStatus.RESOLVED:
        raise SurveyError(f"Unknown {unknown.id} is already resolved: {unknown.resolution}")
    resolving: SurveyEvidence | None = None
    if body.resolve is not None:
        resolving = _evidence(session, [body.resolve.evidence_id], unknown.source_id)[0]
        if resolving.epistemic is not SurveyEpistemic.OBSERVED:
            raise SurveyError(
                "Only observed evidence can resolve an unknown. Reasoning, a plausible reading "
                "or a second inference leaves it open: record what you observed and cite that."
            )
    claimed = session.exec(
        update(SurveyUnknown)
        .where(col(SurveyUnknown.id) == unknown.id, col(SurveyUnknown.revision) == body.revision)
        .values(revision=body.revision + 1, updated_at=utcnow())
    )
    if claimed.rowcount != 1:
        session.rollback()
        current = _get(session, SurveyUnknown, body.unknown_id, "unknown")
        session.refresh(current)
        raise StaleRevision(unknown.id, body.revision, current.revision)
    session.refresh(unknown)
    _link(session, unknown.id, body.supporting, SurveyEvidenceStance.SUPPORTING, unknown.source_id)
    _link(
        session,
        unknown.id,
        body.contradicting,
        SurveyEvidenceStance.CONTRADICTING,
        unknown.source_id,
    )
    if body.authority_required is not None:
        unknown.authority_required = body.authority_required
    if resolving is not None and body.resolve is not None:
        _link(
            session, unknown.id, [resolving.id], SurveyEvidenceStance.SUPPORTING, unknown.source_id
        )
        unknown.status = SurveyUnknownStatus.RESOLVED
        unknown.resolution = body.resolve.resolution
        unknown.resolving_evidence_id = resolving.id
    session.add(unknown)
    session.commit()
    session.refresh(unknown)
    return unknown


def unknown_evidence(session: Session, unknown_id: str) -> list[SurveyUnknownEvidence]:
    return list(
        session.exec(
            select(SurveyUnknownEvidence).where(SurveyUnknownEvidence.unknown_id == unknown_id)
        )
    )


def open_unknowns(
    session: Session, *, source_id: str, pass_id: str | None = None
) -> list[SurveyUnknown]:
    query = select(SurveyUnknown).where(
        SurveyUnknown.source_id == source_id, SurveyUnknown.status == SurveyUnknownStatus.OPEN
    )
    if pass_id is not None:
        query = query.where(SurveyUnknown.opened_by_pass_id == pass_id)
    return list(session.exec(query.order_by(col(SurveyUnknown.created_at))))
