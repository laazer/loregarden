"""Dispatch survey passes from the queue the way other work is dispatched here.

A pass runs as a carrier ticket on the `reference-survey` workflow, queued into a
lane with `auto_approve`, exactly as initiative autopilot queues its picks. A
lane entry and the executor both need a ticket, and a carrier also gives the
pass a place in the board, a run history, and the usual stall detection.

Before a pass is dispatched, in this order:

1. The clone is refreshed (shallow, into the reference cache, never the project
   tree). Nothing in it is ever executed.
2. The licence is classified from the licence files alone, and a source whose
   licence excludes it is refused here. No other file of it has been opened.
3. The tree is mapped, with `lstat` and a walk, at the pinned commit.
4. The pass is given its slice of the map.

Exceptions reach the inbox; everything else runs without a person.
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from loregarden.config import settings
from loregarden.db.session import engine
from loregarden.models.domain import VALID_HIERARCHY, Ticket, TicketState, Workspace
from loregarden.models.domain.enums import utcnow
from loregarden.models.domain.survey_enums import (
    LicenceBasis,
    LicenceUse,
    SurveyPassMode,
    SurveyPassStatus,
    SurveySourceKind,
    SurveySourceStatus,
)
from loregarden.models.domain.survey_tables import SurveyPass, SurveySource
from loregarden.models.domain.work_item_types import WorkItemType
from loregarden.services.queue_lanes import QueueLaneService, least_busy_lane
from loregarden.services.reference_repo_service import (
    ReferenceRepoError,
    clone_or_refresh,
    parse_repo_url,
)
from loregarden.services.reference_survey.coverage import map_tree
from loregarden.services.reference_survey.inbox import report_exception
from loregarden.services.reference_survey.initiatives import (
    InitiativeContext,
    initiative_context,
    open_question,
)
from loregarden.services.reference_survey.licence import LicenceVerdict, classify_tree
from loregarden.services.reference_survey.passes import (
    assign_slice,
    coverage_rows,
    map_leaves,
    store_map,
)
from loregarden.services.reference_survey.queue import (
    next_queued_pass,
    open_passes,
    promote_resolved_sources,
    target_workspaces,
)
from loregarden.services.reference_survey.upstream import refresh_stale
from loregarden.services.ticket_service import TicketService
from loregarden.services.ticket_tags import load_tags
from loregarden.services.workflow_service import WorkflowService
from sqlmodel import Session, col, select

logger = logging.getLogger(__name__)

#: One dispatch at a time in this process: the loop's tick and a person's
#: "Review now" must not both pick the same queued pass while a clone runs.
_DISPATCH_LOCK = threading.Lock()


@dataclass
class _Clock:
    last_tick_at: datetime | None = None


_CLOCK = _Clock()

SURVEY_WORKFLOW_SLUG = "reference-survey"
#: The ticket carrier tickets are filed under, found by this tag in the source's workspace.
CARRIER_PARENT_TAG = "reference-survey-carrier"


@dataclass
class FeedResult:
    promoted: int = 0
    abandoned: int = 0
    dispatched: str | None = None
    refused: str | None = None
    waiting: str = ""
    notes: list[str] = field(default_factory=list)


def carrier_parent(session: Session, workspace_id: str) -> Ticket | None:
    candidates = session.exec(
        select(Ticket).where(
            Ticket.workspace_id == workspace_id,
            col(Ticket.tags_json).contains(CARRIER_PARENT_TAG),
            col(Ticket.state).not_in((TicketState.DONE, TicketState.WONT_DO)),
        )
    ).all()
    return next((t for t in candidates if CARRIER_PARENT_TAG in load_tags(t.tags_json)), None)


def _settle_abandoned(session: Session) -> int:
    """A carrier that closed while its pass was still running: the pass never said
    where it stopped, so the remainder is unknown. Recorded and carded."""
    running = session.exec(
        select(SurveyPass, Ticket)
        .join(Ticket, col(Ticket.id) == SurveyPass.carrier_ticket_id)
        .where(
            SurveyPass.status == SurveyPassStatus.DISPATCHED,
            col(Ticket.state).in_((TicketState.DONE, TicketState.WONT_DO)),
        )
    ).all()
    for survey_pass, carrier in running:
        survey_pass.status = SurveyPassStatus.ABANDONED
        survey_pass.stopped_at = f"Unknown: carrier {carrier.external_id} closed ({carrier.state.value}) before the pass finished."
        survey_pass.finished_at = utcnow()
        session.add(survey_pass)
        source = session.get(SurveySource, survey_pass.source_id)
        if source is not None:
            report_exception(
                session,
                workspace_id=source.workspace_id,
                title=f"Survey pass abandoned: {source.name} pass {survey_pass.ordinal}",
                detail=(
                    f"Carrier {carrier.external_id} closed without finishing the pass, so nothing records "
                    "where it stopped or what it left unread. Request a continuation pass to resume."
                ),
            )
    session.commit()
    return len(running)


def _refuse(
    session: Session,
    survey_pass: SurveyPass,
    source: SurveySource,
    reason: str,
    *,
    refuse_source: bool,
) -> None:
    survey_pass.status = SurveyPassStatus.REFUSED
    survey_pass.refused_reason = reason
    survey_pass.finished_at = utcnow()
    session.add(survey_pass)
    if refuse_source:
        source.status = SurveySourceStatus.REFUSED
        source.refused_reason = reason
    elif not [p for p in open_passes(session, source.id) if p.id != survey_pass.id]:
        source.status = SurveySourceStatus.DONE
    source.updated_at = utcnow()
    session.add(source)
    session.commit()


def _apply_licence(source: SurveySource, verdict: LicenceVerdict, commit_sha: str) -> None:
    source.licence_kind = verdict.kind
    source.licence_basis = verdict.basis
    source.licence_use = verdict.use
    source.licence_name = verdict.name
    source.licence_constraint = verdict.constraint
    source.pinned_commit = commit_sha
    source.fetched_at = utcnow()


def _prepare_repo(session: Session, source: SurveySource) -> tuple[Path, LicenceVerdict]:
    """Clone, classify the licence, and (only if admitted) map. Licence first."""
    path, _branch, head = clone_or_refresh(parse_repo_url(source.url))
    verdict = classify_tree(path)
    if source.licence_basis is LicenceBasis.RECORDED_BY_HAND and (
        verdict.kind is not source.licence_kind or verdict.use is not source.licence_use
    ):
        # A hand call the file contradicts is reported, not silently overwritten
        # (lg-reference-sources-1376). The file at the pinned commit wins.
        report_exception(
            session,
            workspace_id=source.workspace_id,
            title=f"Licence disagreement: {source.name}",
            detail=(
                f"Recorded by hand: {source.licence_name or 'none'} ({source.licence_use.value if source.licence_use else 'unset'}). "
                f"Licence file at {head[:12]}: {verdict.name or 'none'} ({verdict.use.value}). "
                f"The file's reading now applies: {verdict.constraint}"
            ),
        )
    _apply_licence(source, verdict, head)
    session.add(source)
    session.commit()
    if verdict.use is not LicenceUse.EXCLUDED and source.mapped_commit != head:
        store_map(session, source, map_tree(path), head)
    return path, verdict


def _brief(
    source: SurveySource,
    survey_pass: SurveyPass,
    slice_paths: list[str],
    clone: Path | None,
    workspaces: list[str],
    initiatives: list[InitiativeContext],
) -> str:
    where = (
        f"Clone (read-only, never run anything in it): `{clone}`"
        if clone
        else f"Read at: {source.url}"
    )
    return "\n".join(
        [
            f"Reference-survey pass {survey_pass.ordinal} ({survey_pass.mode.value}) on {source.url}.",
            "",
            f"**Pass id:** `{survey_pass.id}` — read the full brief with "
            f"`loregarden_survey_pass action=brief pass_id={survey_pass.id}`.",
            f"**Question:** {survey_pass.question or open_question(workspaces, initiatives)}",
            f"**For workspaces:** {', '.join(workspaces)} — one reading serves all of them; "
            "their own questions are in the brief.",
            *(
                [
                    "**For initiatives:** "
                    + "; ".join(f"{i.external_id} ({i.title})" for i in initiatives)
                    + " — their goals are in the brief."
                ]
                if initiatives
                else []
            ),
            f"**Licence:** {source.licence_constraint or 'unclassified'}",
            where + (f" @ `{source.pinned_commit[:12]}`" if source.pinned_commit else ""),
            f"**Slice ({len(slice_paths)} map paths, budget {survey_pass.file_budget} files):** "
            + (
                ", ".join(f"`{p}`" for p in slice_paths)
                or "chosen against the question from the map"
            ),
            "",
            "Follow `agent_context/skills/reference-survey/SKILL.md`. Findings land through "
            "`loregarden_survey_write`; the pass ends with `loregarden_survey_pass action=finish`.",
        ]
    )


def carrier_type(parent: Ticket) -> WorkItemType:
    """The smallest executable type the carrier parent may hold: a task under a
    capability, a capability under a feature."""
    allowed = VALID_HIERARCHY.get(parent.work_item_type, [])
    for candidate in (WorkItemType.TASK, WorkItemType.CAPABILITY, WorkItemType.FEATURE):
        if candidate in allowed:
            return candidate
    raise ValueError(
        f"The carrier parent {parent.external_id} is a {parent.work_item_type.value}, which holds no "
        "executable work item; tag a feature or capability instead."
    )


def _create_carrier(
    session: Session, source: SurveySource, survey_pass: SurveyPass, parent: Ticket, brief: str
) -> Ticket:
    workspace = session.get(Workspace, source.workspace_id)
    if workspace is None:
        raise ValueError(f"Source {source.id} names a missing workspace")
    asked = survey_pass.question or "open read for what it serves"
    question = asked if len(asked) <= 70 else asked[:67] + "..."
    carrier = TicketService(session).create_ticket(
        workspace_slug=workspace.slug,
        title=f"Survey pass {survey_pass.ordinal}: {source.name} — {question}",
        work_item_type=carrier_type(parent),
        parent_ticket_id=parent.id,
        description=brief,
        acceptance_criteria=[
            "Every map path the pass opened, partly read or deliberately skipped is recorded with "
            "loregarden_survey_pass action=reads, with its file counts.",
            "Every finding that reached a ticket cites evidence, and no absence is claimed from a "
            "corpus not recorded as complete.",
            "The pass is finished with loregarden_survey_pass action=finish, stating its outcome, "
            "where it stopped, and the unread paths worth a later pass.",
        ],
        priority=3,
        created_by="reference-survey",
    )
    WorkflowService(session).set_ticket_workflow_template(carrier, SURVEY_WORKFLOW_SLUG)
    session.commit()
    return carrier


def _dispatch(
    session: Session, survey_pass: SurveyPass, source: SurveySource, result: FeedResult
) -> None:
    parent = carrier_parent(session, source.workspace_id)
    if parent is None:
        result.waiting = "no carrier parent"
        report_exception(
            session,
            workspace_id=source.workspace_id,
            title="Reference survey has no carrier parent",
            detail=(
                f"Passes are queued but none can run: no open ticket in this workspace is tagged "
                f"`{CARRIER_PARENT_TAG}`. Tag the feature survey passes should be filed under."
            ),
        )
        return
    clone: Path | None = None
    if source.kind is SurveySourceKind.REPO:
        try:
            clone, verdict = _prepare_repo(session, source)
        except ReferenceRepoError as exc:
            _refuse(
                session,
                survey_pass,
                source,
                f"Could not clone {source.url}: {exc}",
                refuse_source=False,
            )
            report_exception(
                session,
                workspace_id=source.workspace_id,
                title=f"Survey clone failed: {source.name}",
                detail=str(exc),
            )
            result.refused = survey_pass.id
            return
        if verdict.use is LicenceUse.EXCLUDED:
            reason = f"Licence gate: {verdict.constraint} Read only: {', '.join(verdict.files_read) or 'nothing'}."
            _refuse(session, survey_pass, source, reason, refuse_source=True)
            report_exception(
                session,
                workspace_id=source.workspace_id,
                title=f"Survey source refused by licence: {source.name}",
                detail=reason,
            )
            result.refused = survey_pass.id
            return
    elif source.licence_use is None:
        source.licence_use = LicenceUse.PATTERNS_ONLY
        source.licence_constraint = f"A {source.kind.value}'s terms are not classified by this survey. Read for patterns; copy nothing."
        source.fetched_at = utcnow()
    if survey_pass.mode is SurveyPassMode.SCOPED and not json.loads(survey_pass.slice_json):
        # A scoped pass re-reads against a new question: the whole map is in scope
        # and the budget bounds how much of it the pass may open.
        survey_pass.slice_json = json.dumps(
            [r.path for r in map_leaves(coverage_rows(session, source.id))]
        )
    slice_paths = assign_slice(
        session, survey_pass, file_budget=settings.reference_survey_pass_file_budget
    )
    if source.kind is SurveySourceKind.REPO and not slice_paths:
        _refuse(
            session,
            survey_pass,
            source,
            "Nothing unread is left in the map for this question.",
            refuse_source=False,
        )
        result.notes.append(f"{source.name}: nothing unread; closed")
        return
    carrier = _create_carrier(
        session,
        source,
        survey_pass,
        parent,
        _brief(
            source,
            survey_pass,
            slice_paths,
            clone,
            [w.slug for w, _ in target_workspaces(session, source.id)],
            initiative_context(session, source.id),
        ),
    )
    survey_pass.carrier_ticket_id = carrier.id
    survey_pass.read_commit = source.pinned_commit
    survey_pass.status = SurveyPassStatus.DISPATCHED
    survey_pass.dispatched_at = utcnow()
    session.add(survey_pass)
    session.add(source)
    session.commit()
    lanes = QueueLaneService(session)
    try:
        lanes.add_to_lane(
            ticket_id=carrier.id, slot_number=least_busy_lane(lanes), auto_approve=True
        )
    except ValueError as exc:
        report_exception(
            session,
            workspace_id=source.workspace_id,
            title=f"Survey pass could not be queued: {source.name} pass {survey_pass.ordinal}",
            detail=f"{exc}. The carrier {carrier.external_id} exists; queue it by hand or close it.",
        )
    result.dispatched = survey_pass.id


def running_passes(session: Session) -> int:
    return len(
        session.exec(
            select(SurveyPass.id).where(SurveyPass.status == SurveyPassStatus.DISPATCHED)
        ).all()
    )


def at_limit(running: int) -> str:
    """Why nothing more dispatches while `running` passes run, or "" when one can."""
    limit = settings.reference_survey_max_concurrent_passes
    return f"{running} pass(es) running, at the limit of {limit}" if running >= limit else ""


def feed_once(session: Session) -> FeedResult:
    """One tick: admit resolved sources, settle abandoned passes, dispatch the head."""
    result = FeedResult()
    result.promoted = len(promote_resolved_sources(session))
    result.abandoned = _settle_abandoned(session)
    with _DISPATCH_LOCK:
        result.waiting = at_limit(running_passes(session))
        if result.waiting:
            return result
        head = next_queued_pass(session)
        if head is None:
            result.waiting = "queue empty"
            return result
        if settings.sandbox:
            result.waiting = SANDBOX_WAITING
            return result
        _dispatch(session, head[0], head[1], result)
    return result


SANDBOX_WAITING = "sandbox server: no passes are dispatched here"


def dispatch_now(session: Session, survey_pass: SurveyPass, source: SurveySource) -> FeedResult:
    """Dispatch this queued pass now, out of turn, when nothing stops a dispatch.

    The same limit, sandbox rule, licence gate and carrier parent apply as on a
    tick; `waiting` says which one held it, and the pass stays queued.
    """
    result = FeedResult()
    with _DISPATCH_LOCK:
        session.refresh(survey_pass)
        if survey_pass.status is not SurveyPassStatus.QUEUED:
            result.waiting = f"pass {survey_pass.ordinal} is {survey_pass.status.value}, not queued"
            return result
        result.waiting = at_limit(running_passes(session))
        if not result.waiting and settings.sandbox:
            result.waiting = SANDBOX_WAITING
        if result.waiting:
            return result
        _dispatch(session, survey_pass, source, result)
    return result


def last_tick_at() -> datetime | None:
    """When this process last ran a tick; None before the first one."""
    return _CLOCK.last_tick_at


def _tick() -> None:
    _CLOCK.last_tick_at = utcnow()
    with Session(engine) as session:
        result = feed_once(session)
    # Its own session: a slow or failing GitHub read must not hold up dispatch,
    # and each check records its own answer or reason (`upstream`).
    with Session(engine) as session:
        refresh_stale(session)
    if result.dispatched or result.refused or result.abandoned:
        logger.info("Reference survey feed: %s", result)


async def run_survey_loop(interval_seconds: float) -> None:
    while True:
        try:
            await asyncio.sleep(interval_seconds)
            await asyncio.to_thread(_tick)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 — the loop outliving a bad tick is the point; logged below
            logger.exception("Reference survey loop iteration failed; continuing")


def start_survey_loop(interval_seconds: float | None = None) -> asyncio.Task | None:
    """Start the loop, or None when the interval is non-positive (the off switch)."""
    if interval_seconds is None:
        interval_seconds = settings.reference_survey_interval_seconds
    if interval_seconds <= 0:
        return None
    return asyncio.create_task(run_survey_loop(interval_seconds), name="reference-survey-feeder")
