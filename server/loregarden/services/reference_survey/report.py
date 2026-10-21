"""Read views over the survey record: a pass's brief, and a source's whole history.

"Have we looked at this before, and what did we conclude" used to mean reading
5,000 lines of markdown. `source_status` is that answer as data.
"""

from __future__ import annotations

import json

from loregarden.models.domain import Ticket, Workspace
from loregarden.models.domain.survey_enums import (
    SurveyEvidenceStance,
    SurveyFindingStatus,
    SurveySourceKind,
)
from loregarden.models.domain.survey_tables import (
    SurveyFinding,
    SurveyPass,
    SurveyPassTicket,
    SurveySource,
    SurveyUnknown,
)
from loregarden.services.reference_repo_service import clone_path_for, parse_repo_url
from loregarden.services.reference_survey.errors import SurveyNotFound
from loregarden.services.reference_survey.passes import coverage_report, get_pass, pass_counts_line
from loregarden.services.reference_survey.record import (
    finding_evidence,
    open_unknowns,
    render_finding,
    unknown_evidence,
)
from sqlmodel import Session, col, select

#: What a pass's brief shows of each earlier pass: enough to know where it stopped.
_EARLIER_PASS_FIELDS = frozenset({"ordinal", "label", "question", "outcome", "stopped_at"})


def _source_view(source: SurveySource) -> dict:
    return {
        "source_id": source.id,
        "url": source.url,
        "name": source.name,
        "kind": source.kind.value,
        "status": source.status.value,
        "priority": source.priority,
        "question": source.question,
        "queued_reason": source.queued_reason,
        "licence": {
            "kind": source.licence_kind.value if source.licence_kind else None,
            "basis": source.licence_basis.value if source.licence_basis else None,
            "use": source.licence_use.value if source.licence_use else None,
            "name": source.licence_name,
            "constraint": source.licence_constraint,
        },
        "pinned_commit": source.pinned_commit,
        "refused_reason": source.refused_reason,
    }


def _pass_view(session: Session, survey_pass: SurveyPass) -> dict:
    return {
        "pass_id": survey_pass.id,
        "ordinal": survey_pass.ordinal,
        "label": survey_pass.label,
        "mode": survey_pass.mode.value,
        "status": survey_pass.status.value,
        "question": survey_pass.question,
        "slice": json.loads(survey_pass.slice_json),
        "file_budget": survey_pass.file_budget,
        "read_commit": survey_pass.read_commit,
        "outcome": survey_pass.outcome.value if survey_pass.outcome else None,
        "outcome_note": survey_pass.outcome_note,
        "stopped_at": survey_pass.stopped_at,
        "refused_reason": survey_pass.refused_reason,
        "reads": pass_counts_line(session, survey_pass),
    }


def _unknown_view(session: Session, unknown: SurveyUnknown) -> dict:
    links = unknown_evidence(session, unknown.id)
    return {
        "unknown_id": unknown.id,
        "question": unknown.question,
        "authority_required": unknown.authority_required,
        "status": unknown.status.value,
        "revision": unknown.revision,
        "resolution": unknown.resolution,
        "supporting": [
            link.evidence_id for link in links if link.stance is SurveyEvidenceStance.SUPPORTING
        ],
        "contradicting": [
            link.evidence_id for link in links if link.stance is SurveyEvidenceStance.CONTRADICTING
        ],
    }


def pass_brief(session: Session, pass_id: str) -> dict:
    """Everything a pass needs to start: its question, source, slice, and what came before."""
    survey_pass, source = get_pass(session, pass_id)
    earlier = session.exec(
        select(SurveyPass)
        .where(SurveyPass.source_id == source.id, SurveyPass.ordinal < survey_pass.ordinal)
        .order_by(col(SurveyPass.ordinal))
    ).all()
    clone = ""
    if source.kind is SurveySourceKind.REPO:
        clone = str(clone_path_for(parse_repo_url(source.url)))
    report = coverage_report(session, source.id)
    return {
        "pass": _pass_view(session, survey_pass),
        "source": _source_view(source),
        "clone_path": clone,
        "coverage": {k: v for k, v in report.items() if k not in ("finer_entries",)},
        "earlier_passes": [
            {k: v for k, v in _pass_view(session, p).items() if k in _EARLIER_PASS_FIELDS}
            for p in earlier
        ],
        "open_unknowns": [
            _unknown_view(session, u) for u in open_unknowns(session, source_id=source.id)
        ],
        "rules": [
            "Execute nothing inside the clone; read-only tools only.",
            "Absence needs evidence whose corpus is recorded complete; otherwise claim not_found.",
            "Every count travels with its denominator.",
            "Stop when the question is answered and record where you stopped.",
        ],
    }


def source_status(session: Session, source_id: str) -> dict:
    """A source's whole record: passes, tickets touched, findings, unknowns, coverage."""
    source = session.get(SurveySource, source_id)
    if source is None:
        raise SurveyNotFound(f"No survey source {source_id!r}")
    passes = session.exec(
        select(SurveyPass)
        .where(SurveyPass.source_id == source.id)
        .order_by(col(SurveyPass.ordinal))
    ).all()
    pass_ids = [p.id for p in passes]
    touched = session.exec(
        select(SurveyPassTicket, Ticket)
        .join(Ticket, col(Ticket.id) == SurveyPassTicket.ticket_id, isouter=True)
        .where(col(SurveyPassTicket.pass_id).in_(pass_ids))
    ).all()
    findings = session.exec(
        select(SurveyFinding)
        .where(SurveyFinding.source_id == source.id)
        .order_by(col(SurveyFinding.created_at))
    ).all()
    unknowns = session.exec(select(SurveyUnknown).where(SurveyUnknown.source_id == source.id)).all()
    return {
        "source": _source_view(source),
        "passes": [_pass_view(session, p) for p in passes],
        "tickets": [
            {
                "pass_id": link.pass_id,
                "ticket": ticket.external_id if ticket else None,
                "legacy_ref": link.legacy_ref,
                "action": link.action.value,
                "verified": link.verified,
            }
            for link, ticket in touched
        ],
        "findings": [
            {
                "finding_id": f.id,
                "status": f.status.value,
                "line": render_finding(f, finding_evidence(session, f.id)),
                "replaced_by": f.replaced_by_id,
            }
            for f in findings
        ],
        "retracted": sum(1 for f in findings if f.status is SurveyFindingStatus.RETRACTED),
        "unknowns": [_unknown_view(session, u) for u in unknowns],
        "coverage": coverage_report(session, source.id),
    }


def workspace_id_for(session: Session, slug: str) -> str:
    workspace = session.exec(select(Workspace).where(Workspace.slug == slug)).first()
    if workspace is None:
        raise SurveyNotFound(f"No workspace {slug!r}")
    return workspace.id
