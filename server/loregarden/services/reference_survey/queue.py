"""The survey queue: sources in priority order, and the passes waiting on them.

A source enters as PROPOSED with an inbox card; nothing reads it until a person
approves. From then on the queue drains unattended: an approved source gets an
initial pass, a finished pass that left its question open and named what to read
next gets a continuation, and anyone may ask for a scoped re-read against one new
question. The feeder (`feeder.py`) dispatches the head of the queue.
"""

from __future__ import annotations

import json
from urllib.parse import urlparse

from loregarden.models.domain import Approval, ApprovalStatus, Workspace
from loregarden.models.domain.enums import utcnow
from loregarden.models.domain.survey_enums import (
    SurveyPassMode,
    SurveyPassStatus,
    SurveySourceKind,
    SurveySourceStatus,
)
from loregarden.models.domain.survey_tables import SurveyPass, SurveySource
from loregarden.services.reference_repo_service import ReferenceRepoError, parse_repo_url
from loregarden.services.reference_survey.errors import SurveyError, SurveyNotFound
from loregarden.services.reference_survey.inbox import file_card
from loregarden.services.reference_survey.schemas import PassRequestIn, SourceIn
from sqlalchemy import func
from sqlmodel import Session, col, select

#: Continuations chase one question at most this many passes before the source is
#: closed with what remains named. Paging is bounded by the question, not by
#: exhaustion: openharness took 23 passes because nothing said when to stop.
MAX_PASSES_PER_QUESTION = 5

_OPEN_PASS = (SurveyPassStatus.QUEUED, SurveyPassStatus.DISPATCHED)


def _canonical_url(kind: SurveySourceKind, raw: str) -> str:
    if kind is SurveySourceKind.REPO:
        try:
            parsed = parse_repo_url(raw)
        except ReferenceRepoError as exc:
            raise SurveyError(f"Not a repository URL this survey can clone: {exc}") from exc
        # One spelling per repository, so `.git`, ssh and https forms are one source.
        return f"https://{parsed.host}/{parsed.owner}/{parsed.name}"
    parsed = urlparse(raw.strip())
    if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password:
        raise SurveyError(
            "A site or released app is named by an https URL with no credentials in it"
        )
    return raw.strip()


def admission_title(source: SurveySource) -> str:
    return f"Admit reference source: {source.url}"


def enqueue_source(session: Session, body: SourceIn) -> SurveySource:
    """Propose a source. It is read only after a person approves the card this files."""
    workspace = session.exec(select(Workspace).where(Workspace.slug == body.workspace_slug)).first()
    if workspace is None:
        raise SurveyNotFound(f"No workspace {body.workspace_slug!r}")
    url = _canonical_url(body.kind, body.url)
    existing = session.exec(
        select(SurveySource).where(
            SurveySource.workspace_id == workspace.id, SurveySource.url == url
        )
    ).first()
    if (
        existing is not None
        and existing.status is SurveySourceStatus.PROPOSED
        and existing.approval_id is None
    ):
        # An imported candidate nobody has proposed yet: this is its proposal.
        return _file_admission(session, existing, body)
    if existing is not None:
        raise SurveyError(
            f"{url} is already in the queue ({existing.status.value}, source {existing.id}). "
            "To read it again, request a continuation or scoped pass on that source."
        )
    source = SurveySource(
        workspace_id=workspace.id,
        kind=body.kind,
        url=url,
        name=body.name or url.rstrip("/").rsplit("/", 1)[-1],
        question=body.question,
        queued_reason=body.queued_reason,
        priority=body.priority,
    )
    session.add(source)
    session.flush()
    return _file_admission(session, source, body)


def _file_admission(session: Session, source: SurveySource, body: SourceIn) -> SurveySource:
    source.question = body.question
    source.queued_reason = body.queued_reason
    source.priority = body.priority
    card = file_card(
        session,
        workspace_id=source.workspace_id,
        title=admission_title(source),
        impact=(
            f"A {source.kind.value} proposed for the reference survey.\n\n"
            f"Question: {body.question}\nWhy: {body.queued_reason}\n\n"
            "Approve to queue it: its licence is classified before anything else is read, "
            "and passes then run unattended, appending findings to tickets. Reject to refuse it."
        ),
        level="medium",
    )
    source.approval_id = card.id
    source.updated_at = utcnow()
    session.add(source)
    session.commit()
    session.refresh(source)
    return source


def _next_ordinal(session: Session, source_id: str) -> int:
    current = session.exec(
        select(func.max(SurveyPass.ordinal)).where(SurveyPass.source_id == source_id)
    ).one()
    return (current or 0) + 1


def _new_pass(
    session: Session, source: SurveySource, mode: SurveyPassMode, question: str, paths: list[str]
) -> SurveyPass:
    survey_pass = SurveyPass(
        source_id=source.id,
        ordinal=_next_ordinal(session, source.id),
        mode=mode,
        question=question,
        slice_json=json.dumps(paths),
    )
    session.add(survey_pass)
    return survey_pass


def promote_resolved_sources(session: Session) -> list[SurveySource]:
    """Move every proposed source whose card a person has resolved."""
    changed: list[SurveySource] = []
    proposed = session.exec(
        select(SurveySource, Approval)
        .join(Approval, col(Approval.id) == SurveySource.approval_id)
        .where(
            SurveySource.status == SurveySourceStatus.PROPOSED,
            Approval.status != ApprovalStatus.PENDING,
        )
    ).all()
    for source, card in proposed:
        if card.status is ApprovalStatus.APPROVED:
            source.status = SurveySourceStatus.ACTIVE
            _new_pass(session, source, SurveyPassMode.INITIAL, source.question, [])
        else:
            source.status = SurveySourceStatus.REFUSED
            source.refused_reason = f"A person {card.status.value} the admission card."
        source.updated_at = utcnow()
        session.add(source)
        changed.append(source)
    if changed:
        session.commit()
    return changed


def request_pass(session: Session, body: PassRequestIn) -> SurveyPass:
    """Queue a continuation or scoped pass on a source already admitted."""
    source = session.get(SurveySource, body.source_id)
    if source is None:
        raise SurveyNotFound(f"No survey source {body.source_id!r}")
    if source.status not in (SurveySourceStatus.ACTIVE, SurveySourceStatus.DONE):
        raise SurveyError(
            f"Source {source.url} is {source.status.value}; only an admitted source takes new passes."
        )
    if body.mode is SurveyPassMode.INITIAL:
        raise SurveyError("An initial pass is queued by admitting the source, not requested")
    source.status = SurveySourceStatus.ACTIVE
    source.updated_at = utcnow()
    session.add(source)
    survey_pass = _new_pass(session, source, body.mode, body.question, body.paths)
    session.commit()
    session.refresh(survey_pass)
    return survey_pass


def passes_on_question(session: Session, source_id: str, question: str) -> int:
    return len(
        session.exec(
            select(SurveyPass.id).where(
                SurveyPass.source_id == source_id, SurveyPass.question == question
            )
        ).all()
    )


def queue_continuation(session: Session, source: SurveySource, question: str) -> SurveyPass | None:
    """After a pass that left its question partly open: one more, within the cap.

    Returns None when the cap is reached; the caller closes the source and the
    record keeps the unread list for whoever asks again.
    """
    if passes_on_question(session, source.id, question) >= MAX_PASSES_PER_QUESTION:
        return None
    return _new_pass(session, source, SurveyPassMode.CONTINUATION, question, [])


def open_passes(session: Session, source_id: str) -> list[SurveyPass]:
    return list(
        session.exec(
            select(SurveyPass).where(
                SurveyPass.source_id == source_id, col(SurveyPass.status).in_(_OPEN_PASS)
            )
        )
    )


def next_queued_pass(session: Session) -> tuple[SurveyPass, SurveySource] | None:
    """The head of the queue: lowest source priority, then the oldest pass."""
    row = session.exec(
        select(SurveyPass, SurveySource)
        .join(SurveySource, col(SurveySource.id) == SurveyPass.source_id)
        .where(
            SurveyPass.status == SurveyPassStatus.QUEUED,
            SurveySource.status == SurveySourceStatus.ACTIVE,
        )
        .order_by(col(SurveySource.priority), col(SurveyPass.queued_at), col(SurveyPass.ordinal))
    ).first()
    return None if row is None else (row[0], row[1])


def queue_listing(
    session: Session, *, workspace_id: str | None = None, limit: int = 50
) -> list[dict]:
    """Waiting and in-flight passes in dispatch order, and proposed sources."""
    query = (
        select(SurveyPass, SurveySource)
        .join(SurveySource, col(SurveySource.id) == SurveyPass.source_id)
        .where(col(SurveyPass.status).in_(_OPEN_PASS))
        .order_by(
            col(SurveyPass.status).desc(), col(SurveySource.priority), col(SurveyPass.queued_at)
        )
    )
    if workspace_id is not None:
        query = query.where(SurveySource.workspace_id == workspace_id)
    rows = [
        {
            "pass_id": p.id,
            "source_id": s.id,
            "url": s.url,
            "ordinal": p.ordinal,
            "mode": p.mode.value,
            "status": p.status.value,
            "question": p.question,
            "priority": s.priority,
            "carrier_ticket_id": p.carrier_ticket_id,
        }
        for p, s in session.exec(query.limit(limit)).all()
    ]
    proposed = session.exec(
        select(SurveySource)
        .where(SurveySource.status == SurveySourceStatus.PROPOSED)
        .order_by(col(SurveySource.priority), col(SurveySource.created_at))
    ).all()
    rows.extend(
        {
            "source_id": s.id,
            "url": s.url,
            "status": SurveySourceStatus.PROPOSED.value,
            "approval_id": s.approval_id,
            "priority": s.priority,
        }
        for s in proposed
        if workspace_id is None or s.workspace_id == workspace_id
    )
    return rows
