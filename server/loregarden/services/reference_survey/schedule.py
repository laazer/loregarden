"""When the next reference will be reviewed, and reviewing one now.

The feeder dispatches the head of the queue once per tick, when fewer than the
limit are running and its workspace has a carrier parent. This module says
which pass that is, what (if anything) holds it, and when the next tick is due;
and lets a person dispatch one source out of turn ("Review now") under the same
rules, so the answer to "why has it not started?" is the same in both places.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from loregarden.config import settings
from loregarden.models.domain import Ticket, Workspace
from loregarden.models.domain.enums import utcnow
from loregarden.models.domain.survey_enums import (
    SurveyPassMode,
    SurveyPassStatus,
    SurveySourceStatus,
)
from loregarden.models.domain.survey_tables import SurveyPass, SurveySource
from loregarden.services.reference_survey import feeder
from loregarden.services.reference_survey.errors import SurveyError, SurveyNotFound, brief_reason
from loregarden.services.reference_survey.initiatives import follow, resolve_initiatives
from loregarden.services.reference_survey.queue import open_passes, request_pass, serve_workspaces
from loregarden.services.reference_survey.schemas import PassRequestIn
from pydantic import BaseModel, Field
from sqlmodel import Session, col, select

#: "Review now" puts the source first in line: lower priority dispatches first.
FIRST_IN_LINE = 0


class RunningPass(BaseModel):
    source_id: str
    name: str
    ordinal: int
    carrier: str | None


class NextUp(BaseModel):
    source_id: str
    name: str
    ordinal: int
    mode: str
    #: Why it is not dispatching; "" when it goes at the next tick.
    waiting: str
    #: Whether "Review now" would start it: nothing but the clock holds it.
    can_start_now: bool


class QueueSchedule(BaseModel):
    #: False when this server runs no survey loop; `disabled_reason` says why.
    enabled: bool
    disabled_reason: str
    interval_seconds: float
    #: When this server last ran a tick; None until its first one.
    last_tick_at: datetime | None
    next_tick_at: datetime | None
    max_concurrent: int
    running: list[RunningPass]
    next: NextUp | None
    #: Every queued source's place in line (1 is next), across all workspaces.
    positions: dict[str, int]


def _queued_in_order(session: Session) -> list[tuple[SurveyPass, SurveySource]]:
    rows = session.exec(
        select(SurveyPass, SurveySource)
        .join(SurveySource, col(SurveySource.id) == SurveyPass.source_id)
        .where(
            SurveyPass.status == SurveyPassStatus.QUEUED,
            SurveySource.status == SurveySourceStatus.ACTIVE,
        )
        .order_by(col(SurveySource.priority), col(SurveyPass.queued_at), col(SurveyPass.ordinal))
    ).all()
    return [(p, s) for p, s in rows]


def _no_carrier(session: Session, source: SurveySource) -> str:
    if feeder.carrier_parent(session, source.workspace_id) is not None:
        return ""
    workspace = session.get(Workspace, source.workspace_id)
    slug = workspace.slug if workspace else source.workspace_id
    return (
        f"no open ticket in {slug} is tagged `{feeder.CARRIER_PARENT_TAG}`; tag the feature "
        "survey passes should be filed under"
    )


def _blocker(session: Session, source: SurveySource, running: int) -> str:
    """What stops a dispatch of `source` now, in the order the bar and Review now
    both report it; "" when nothing does. The clock is not a blocker."""
    return (
        (feeder.SANDBOX_WAITING if settings.sandbox else "")
        or feeder.at_limit(running)
        or _no_carrier(session, source)
    )


def _running(session: Session) -> list[RunningPass]:
    rows = session.exec(
        select(SurveyPass, SurveySource, Ticket)
        .join(SurveySource, col(SurveySource.id) == SurveyPass.source_id)
        .join(Ticket, col(Ticket.id) == SurveyPass.carrier_ticket_id, isouter=True)
        .where(SurveyPass.status == SurveyPassStatus.DISPATCHED)
        .order_by(col(SurveyPass.dispatched_at))
    ).all()
    return [
        RunningPass(
            source_id=s.id, name=s.name, ordinal=p.ordinal, carrier=t.external_id if t else None
        )
        for p, s, t in rows
    ]


def queue_schedule(session: Session) -> QueueSchedule:
    interval = settings.reference_survey_interval_seconds
    if settings.sandbox:
        disabled = "this is a sandbox server: it runs no survey loop and dispatches nothing"
    elif interval <= 0:
        disabled = "the survey loop is switched off on this server"
    else:
        disabled = ""
    enabled = not disabled
    last = feeder.last_tick_at()
    queued = _queued_in_order(session)
    running = _running(session)
    next_up = None
    if queued:
        survey_pass, source = queued[0]
        # What "Review now" cannot get past; a loop that is merely off it can.
        blocked = _blocker(session, source, len(running))
        next_up = NextUp(
            source_id=source.id,
            name=source.name,
            ordinal=survey_pass.ordinal,
            mode=survey_pass.mode.value,
            waiting=blocked or disabled,
            can_start_now=not blocked,
        )
    positions: dict[str, int] = {}
    for _, source in queued:
        positions.setdefault(source.id, len(positions) + 1)
    return QueueSchedule(
        enabled=enabled,
        disabled_reason=disabled,
        interval_seconds=interval,
        last_tick_at=last,
        next_tick_at=last + timedelta(seconds=interval) if enabled and last else None,
        max_concurrent=settings.reference_survey_max_concurrent_passes,
        running=running,
        next=next_up,
        positions=positions,
    )


class ReviewNowIn(BaseModel):
    #: What a new pass on a surveyed source answers; a queued pass keeps its own.
    question: str = ""
    #: Why a refused source should be read after all. Required to reopen one;
    #: kept on its record beside the refusal it overrides.
    reason: str = ""
    #: Workspaces the pass should read for as well; the source serves them from now on.
    workspace_slugs: list[str] = Field(default_factory=list)
    #: Initiatives to follow the source (its open tickets join them), by UUID or external id.
    initiative_ids: list[str] = Field(default_factory=list)


class ReviewNowResult(BaseModel):
    started: bool
    ordinal: int
    #: The carrier ticket the pass runs as, once started.
    carrier: str | None
    #: Why it did not start; it stays first in line. "" when it started or was refused.
    waiting: str
    #: Set when the dispatch itself closed the pass (licence, nothing unread).
    refused: str
    #: Workspaces this call made the source serve.
    workspaces_added: list[str]
    #: Initiatives this call linked, by external id.
    initiatives_linked: list[str]


def _refuse_unrunnable(session: Session, source: SurveySource, reason: str) -> None:
    """Refuse, before anything is written, a source no review can start on now."""
    if source.status is SurveySourceStatus.REFUSED and not reason.strip():
        raise SurveyError(
            f"{source.name} was refused ({brief_reason(source.refused_reason)}). "
            "Say why it should be read after all to review it again."
        )
    if source.status is SurveySourceStatus.PROPOSED:
        raise SurveyError(
            f"{source.name} has not been admitted: approve its card in the inbox first."
            if source.approval_id
            else f"{source.name} has not been proposed: add it to the queue first."
        )
    reading = next(
        (p for p in open_passes(session, source.id) if p.status is SurveyPassStatus.DISPATCHED),
        None,
    )
    if reading is not None:
        raise SurveyError(f"Pass {reading.ordinal} is reading {source.name} now.")


def _reopen(session: Session, source: SurveySource, reason: str) -> None:
    """Put a refused source back in the queue on a person's reason.

    The refusal and the reason stay on its record, so the next reader sees both.
    The licence gate still runs before its pass reads anything: a source refused
    for its licence is refused again unless the licence file has changed.
    """
    stamp = utcnow().strftime("%Y-%m-%d")
    note = (
        f"**Reviewed again {stamp}.** It had been refused: "
        f"{source.refused_reason or 'no reason recorded'}. Reason to read it now: {reason.strip()}"
    )
    source.notes = f"{source.notes}\n\n{note}".strip()
    source.queued_reason = reason.strip()
    source.refused_reason = ""
    source.approval_id = None
    source.status = SurveySourceStatus.ACTIVE
    source.updated_at = utcnow()
    session.add(source)
    session.commit()


def _pass_to_run(session: Session, source: SurveySource, question: str) -> SurveyPass:
    waiting = sorted(open_passes(session, source.id), key=lambda p: p.ordinal)
    if waiting:
        return waiting[0]
    # No question is fine: the pass reads for the workspaces and initiatives it serves.
    asked = question.strip() or source.question
    return request_pass(
        session, PassRequestIn(source_id=source.id, mode=SurveyPassMode.SCOPED, question=asked)
    )


def review_now(session: Session, source_id: str, body: ReviewNowIn) -> ReviewNowResult:
    """Put the source first in line and dispatch its pass now if nothing holds it.

    A queued pass is dispatched as it is; a surveyed source gets a scoped pass
    against `body.question` (or its own question) first.
    """
    source = session.get(SurveySource, source_id)
    if source is None:
        raise SurveyNotFound(f"No survey source {source_id!r}")
    _refuse_unrunnable(session, source, body.reason)
    initiatives = resolve_initiatives(session, body.initiative_ids)
    added = (
        serve_workspaces(
            session, source, body.workspace_slugs, body.question.strip() or source.question, ""
        )
        if body.workspace_slugs
        else []
    )
    if source.status is SurveySourceStatus.REFUSED:
        _reopen(session, source, body.reason)
    survey_pass = _pass_to_run(session, source, body.question)
    linked = follow(session, source.id, initiatives, actor="operator")
    source.priority = FIRST_IN_LINE
    session.add(source)
    session.commit()
    waiting = _blocker(session, source, feeder.running_passes(session))
    result = feeder.FeedResult(waiting=waiting)
    if not waiting:
        result = feeder.dispatch_now(session, survey_pass, source)
    session.refresh(survey_pass)
    carrier = (
        session.get(Ticket, survey_pass.carrier_ticket_id)
        if survey_pass.carrier_ticket_id
        else None
    )
    return ReviewNowResult(
        started=result.dispatched == survey_pass.id,
        ordinal=survey_pass.ordinal,
        carrier=carrier.external_id if carrier else None,
        waiting=result.waiting,
        refused=survey_pass.refused_reason if result.refused == survey_pass.id else "",
        workspaces_added=added,
        initiatives_linked=[link.initiative for link in linked],
    )
