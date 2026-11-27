"""The ticket page's view of one ticket: `TicketSummary` and `TicketDetail`.

Moved out of `api/tickets.py` so the `ticket_detail` operation can build it
without a service importing a route module, and so the write routes that reply
with a ticket's detail build it the same way the read does.
"""

import json
import logging
from collections.abc import Sequence
from typing import Any

from loregarden.core.workflow_loader import stage_display_name
from loregarden.models.domain import (
    AgentRun,
    Artifact,
    OrchestrationRunView,
    RunStatus,
    StageBudgetArtifactKind,
    StageStatus,
    Ticket,
    TicketActivity,
    TicketDependencyRef,
    TicketDetail,
    TicketSummary,
    WorkflowTransitionView,
    comparable_utc,
)
from loregarden.services import artifact_service
from loregarden.services.acceptance_criteria import load_checked, load_criteria
from loregarden.services.artifact_service import diff_for_read, tests_for_read
from loregarden.services.base_delivery import delivery_of
from loregarden.services.block_classification import block_message_for
from loregarden.services.blocked_on import (
    StuckPage,
)
from loregarden.services.cli_settings import (
    get_ticket_orchestration_runtime,
)
from loregarden.services.compatibility_posture import resolve_compatibility_posture
from loregarden.services.dependency_hold import (
    still_holding,
    unmet_prerequisites,
)
from loregarden.services.hierarchy_service import child_count
from loregarden.services.log_storage import read_log_lines
from loregarden.services.orchestration import OrchestrationService
from loregarden.services.orchestration_callbacks import OrchestrationCallbackService
from loregarden.services.run_errors import normalize_timeout_stderr
from loregarden.services.studio_routing import ticket_stage_agent
from loregarden.services.ticket_activity import (
    activity_for,
)
from loregarden.services.ticket_dependencies import (
    TicketDependencyService,
)
from loregarden.services.ticket_pull_request import ships_with_branch
from loregarden.services.ticket_relations import TicketRelationService
from loregarden.services.ticket_split_view import ticket_split
from loregarden.services.ticket_state_service import RESOLVED_STATES
from loregarden.services.ticket_tags import load_tags
from loregarden.services.workflow_routing import normalize_transitions
from loregarden.services.workflow_service import (
    resolve_ticket_transitions,
    workspace_for,
)
from pydantic import BaseModel, ConfigDict, TypeAdapter, ValidationError
from sqlmodel import Session, col, select

# Bound, not ImportFrom: py_organization rejects importing a `_` name, and this
# ticket forbids promoting `_test_artifact_is_valid` in artifact_service.
_test_artifact_is_valid = artifact_service._test_artifact_is_valid

logger = logging.getLogger(__name__)


def _latest_run_code(session: Session, ticket_id: str) -> str:
    # The column, not the row: `agent_runs.stdout` holds a run's whole
    # transcript, and every ticket summary calls this — once per ticket on the
    # list endpoint, which made a page of summaries fetch a page of transcripts.
    run_code = session.exec(
        select(AgentRun.run_code)
        .where(AgentRun.ticket_id == ticket_id)
        .order_by(AgentRun.created_at.desc())
    ).first()
    return run_code or ""


#: SQLite's default parameter limit is 999; a page of tickets is bigger than that.
_ID_CHUNK = 400


def latest_run_codes(session: Session, ticket_ids: list[str]) -> dict[str, str]:
    """Every ticket's latest run code, in one scan per chunk rather than per ticket.

    `agent_runs` has no index behind `ORDER BY created_at DESC`, so asking
    `_latest_run_code` once per row scanned the table once per row — measured on
    a copy of the live database, 837 queries and 17.5s of an 85s ticket list.
    Ascending order lets a later run overwrite an earlier one, so what lands is
    the latest.

    A ticket with no runs is absent from the result; callers pass `""`, which is
    what `_latest_run_code` answers for the same case.
    """
    codes: dict[str, str] = {}
    for start in range(0, len(ticket_ids), _ID_CHUNK):
        chunk = ticket_ids[start : start + _ID_CHUNK]
        rows = session.exec(
            select(AgentRun.ticket_id, AgentRun.run_code)
            .where(col(AgentRun.ticket_id).in_(chunk))
            .order_by(col(AgentRun.created_at))
        ).all()
        for ticket_id, run_code in rows:
            codes[ticket_id] = run_code
    return codes


def ticket_summary(
    session: Session,
    ticket: Ticket,
    activity: TicketActivity | None = None,
    run_code: str | None = None,
    stuck: StuckPage | None = None,
    children: int | None = None,
) -> TicketSummary:
    """``activity``, ``run_code``, ``stuck`` and ``children`` are passed in by list endpoints,
    which resolve the whole page in one batch each; single-ticket callers let
    them resolve themselves."""
    stuck = stuck or StuckPage.of(session, [ticket])
    ws = workspace_for(session, ticket.workspace_id)
    orch = OrchestrationService(session)
    template = orch.get_template_for_ticket(ticket)
    # Derived, not read off the row: the column is only authoritative once
    # something has run, and the read path used to backfill it as a side effect
    # of serializing (lg-workflow-integrity-606).
    stage_key, stage_status = orch.stage_cursor_for_read(ticket)
    stage_name = ""
    if template and stage_key:
        stage_name = stage_display_name(template, stage_key)
    stages = orch.stage_views_for_read(ticket)
    return TicketSummary(
        id=ticket.id,
        external_id=ticket.external_id,
        legacy_external_id=ticket.legacy_external_id,
        title=ticket.title,
        state=ticket.state,
        priority=ticket.priority,
        workspace_slug=ws.slug if ws else "",
        workflow_stage_key=stage_key,
        workflow_stage_status=stage_status,
        workflow_stage_name=stage_name,
        run_code=run_code if run_code is not None else _latest_run_code(session, ticket.id),
        work_item_type=ticket.work_item_type,
        parent_ticket_id=ticket.parent_ticket_id,
        milestone=ticket.milestone,
        branch=ticket.branch,
        tags=load_tags(ticket.tags_json),
        child_count=children if children is not None else child_count(session, ticket.id),
        current_stage_agent=ticket_stage_agent(session, ticket),
        activity=activity if activity is not None else activity_for(session, ticket.id),
        stages=stages,
        block_kind=ticket.block_kind,
        blocked_on=stuck.blocked_on.get(ticket.id),
        agent_retry=stuck.agent_retry.get(ticket.id),
        waiting_on=stuck.waiting_on.get(ticket.id, []),
        root_blockers=stuck.root_blockers.get(ticket.id, []),
    )


def _run_statuses(session: Session, run_ids: list[str | None]) -> dict[str, RunStatus]:
    """Status of each named run, by id — the two columns, not the rows."""
    wanted = {run_id for run_id in run_ids if run_id}
    if not wanted:
        return {}
    return dict(
        session.exec(select(AgentRun.id, AgentRun.status).where(col(AgentRun.id).in_(wanted))).all()
    )


def _apply_log_artifacts(
    session: Session,
    ticket_id: str,
    log_artifacts: list[Artifact],
    grouped: dict,
) -> bool:
    """Populate ``grouped['logs']`` / ``grouped['live']`` from a ticket's log
    artifacts.

    Returns ``True`` when the caller should stop and return immediately — an
    active run exists but has not emitted its log artifact yet — matching the
    original early return so the workspace diff/test backfill is skipped.
    """
    running_runs = session.exec(
        select(AgentRun.id, AgentRun.status)
        .where(AgentRun.ticket_id == ticket_id, AgentRun.status == RunStatus.RUNNING)
        .order_by(AgentRun.created_at.desc())
    ).all()
    active_run = running_runs[0] if running_runs else None

    if not active_run:
        active_run = session.exec(
            select(AgentRun.id, AgentRun.status)
            .where(
                AgentRun.ticket_id == ticket_id,
                AgentRun.status == RunStatus.AWAITING_PERMISSION,
            )
            .order_by(AgentRun.created_at.desc())
        ).first()

    best: Artifact | None = None
    if active_run:
        active_run_id, active_run_status = active_run
        best = next((art for art in log_artifacts if art.run_id == active_run_id), None)
        if not best:
            grouped["logs"] = []
            grouped["live"] = (
                "Awaiting your approval in Triage or Inbox…"
                if active_run_status == RunStatus.AWAITING_PERMISSION
                else "Agent running…"
            )
            return True

    active_log_statuses = {RunStatus.RUNNING, RunStatus.AWAITING_PERMISSION}
    # One read for every run these artifacts name. This used to sit inside the
    # sort comparator below, so choosing among N log artifacts fetched N whole
    # runs — transcripts included — to read N statuses.
    run_statuses = _run_statuses(session, [art.run_id for art in log_artifacts])

    if not best:

        def _log_sort_key(item: Artifact) -> tuple:
            body = json.loads(item.content_json or "{}")
            status = run_statuses.get(item.run_id or "")
            is_live = bool(body.get("live")) and status in active_log_statuses
            live_rank = 0 if is_live else 1
            return (live_rank, -item.created_at.timestamp())

        best = sorted(log_artifacts, key=_log_sort_key)[0]

    content = json.loads(best.content_json or "{}")
    grouped["logs"] = read_log_lines(session, best.run_id or "", content)
    live = content.get("live")
    status = run_statuses.get(best.run_id or "")
    if live and status is not None and status not in active_log_statuses:
        live = None
    grouped["live"] = live
    return False


def _retry_payload(art: Artifact) -> dict:
    """A transient-retry marker's payload, or {} if it will not parse.

    Its own reader because `_artifacts_grouped` parses every artifact's
    `content_json` in one pass and these are collected in a separate filter —
    see there for why. An unparseable marker yields an empty message rather than
    breaking the whole ticket payload: the row's existence is the load-bearing
    part, since the count is what says the stage ran more than once.
    """
    try:
        return json.loads(art.content_json or "{}")
    except json.JSONDecodeError:
        logger.warning(
            "Transient-retry marker %s does not parse; rendering it without a message", art.id
        )
        return {}


class _ErrorArtifactContent(BaseModel):
    """An error artifact's payload: a message, plus whatever its writer recorded."""

    model_config = ConfigDict(extra="allow")

    message: str = ""


def _current_error(
    session: Session, ticket_id: str, error_artifacts: list[Artifact]
) -> dict | None:
    """The latest error artifact, while it still describes where the ticket is.

    Error artifacts are never cleared, so "the latest one" alone kept a block
    from hours ago on screen, labelled Blocked, after later stages ran green
    (lg-durable-remote-336: a 23:27 implement block, test-break succeeded at
    23:38, implement running again). Once any run has started since the error
    was written, the work moved on and the error is history — it stays on the
    Timeline as an output, and a live block is in `blocking_issues`.
    """
    if not error_artifacts:
        return None
    latest = max(error_artifacts, key=lambda a: comparable_utc(a.created_at))
    newer_run = session.exec(
        select(AgentRun.id)
        .where(AgentRun.ticket_id == ticket_id)
        .where(col(AgentRun.created_at) > latest.created_at)
        .limit(1)
    ).first()
    if newer_run is not None:
        return None
    try:
        content = _ErrorArtifactContent.model_validate_json(latest.content_json or "{}")
    except ValidationError:
        # Still shown: an operator reading a raw payload beats a block with no reason.
        logger.warning("Error artifact %s has an unreadable payload", latest.id, exc_info=True)
        return {"message": latest.content_json or ""}
    content.message = normalize_timeout_stderr(content.message)
    return content.model_dump()


class _ContextArtifactContent(BaseModel):
    """A context artifact's payload: run-context artifacts nest their sections
    under `sections`; a stage report is already one flat section."""

    model_config = ConfigDict(extra="allow")

    sections: list[Any] | None = None


_SECTION = TypeAdapter(dict[str, Any])


def _context_sections(content: dict) -> list[dict]:
    try:
        nested = _ContextArtifactContent.model_validate(content).sections
    except ValidationError:
        nested = None  # `sections` that is not a list: the payload is the section
    if nested is None:
        return [content]
    return [section for section in map(_section, nested) if section is not None]


def _section(item: Any) -> dict | None:
    try:
        return _SECTION.validate_python(item)
    except ValidationError:  # silent-ok: not a failure; a section that is not an object has no fields to show and was always skipped
        return None


def artifacts_grouped(session: Session, ticket: Ticket) -> dict:
    grouped: dict = {
        "diff": None,
        "logs": [],
        "tests": None,
        "context": [],
        "live": None,
        "error": None,
        "pr": None,
        # Stages re-dispatched automatically after an infrastructure death. A
        # list, not a latest-wins single value like `error`: the count IS the
        # information, and the ticket is deliberately NOT blocked while these
        # accumulate, so nothing else on this payload reveals that a stage ran
        # more than once.
        "transient_retries": [],
    }
    artifacts = session.exec(select(Artifact).where(Artifact.ticket_id == ticket.id)).all()
    log_artifacts, error_artifacts = _fold_artifacts(artifacts, grouped)
    grouped["transient_retries"] = _transient_retries(artifacts)
    grouped["error"] = _current_error(session, ticket.id, error_artifacts)
    if log_artifacts and _apply_log_artifacts(session, ticket.id, log_artifacts, grouped):
        return grouped
    _backfill(session, ticket, grouped)
    return grouped


def _fold_artifacts(
    artifacts: Sequence[Artifact], grouped: dict
) -> tuple[list[Artifact], list[Artifact]]:
    """Fold the latest-wins kinds into `grouped`; return the logs and errors,
    which are chosen among afterwards rather than folded."""
    log_artifacts: list[Artifact] = []
    error_artifacts: list[Artifact] = []
    for art in artifacts:
        content = json.loads(art.content_json or "{}")
        if art.kind == "diff":
            grouped["diff"] = content
        elif art.kind == "log":
            log_artifacts.append(art)
        elif art.kind == "error":
            error_artifacts.append(art)
        elif art.kind == "test":
            grouped["tests"] = content
        elif art.kind == "context":
            grouped["context"].extend(_context_sections(content))
        elif art.kind == "pr":
            grouped["pr"] = content
    return log_artifacts, error_artifacts


def _transient_retries(artifacts: Sequence[Artifact]) -> list[dict]:
    """Selected, not folded: unlike `error`, every row is kept, because the
    count is the information."""
    return sorted(
        (
            {
                "stage_key": art.title.removeprefix("stage-transient-retry:"),
                "message": _retry_payload(art).get("message", ""),
                "reason": _retry_payload(art).get("reason", ""),
                "at": art.created_at.isoformat() if art.created_at else "",
            }
            for art in artifacts
            if art.kind == StageBudgetArtifactKind.TRANSIENT_RETRY
        ),
        key=lambda row: row["at"],
    )


def _backfill(session: Session, ticket: Ticket, grouped: dict) -> None:
    """Fill in a missing or stale diff and test result, and say when a stage
    is waiting on the operator. Stores nothing: the run that changes the work
    stores them (`refresh_execution_artifacts`)."""
    workspace = workspace_for(session, ticket.workspace_id)
    if workspace:
        grouped["diff"] = diff_for_read(session, ticket=ticket, workspace=workspace)
        if not grouped["tests"] or not _test_artifact_is_valid(grouped["tests"] or {}):
            grouped["tests"] = tests_for_read(session, ticket=ticket)
    if not grouped.get("live") and ticket.workflow_stage_status == StageStatus.AWAITING:
        grouped["live"] = "Awaiting your approval in Triage or Inbox…"


def _active_orchestration_view(session: Session, ticket: Ticket) -> OrchestrationRunView | None:
    active = OrchestrationCallbackService(session).get_active_orchestration_run(ticket.id)
    return OrchestrationRunView.model_validate(active, from_attributes=True) if active else None


def _dependency_edge_ids(session: Session, ticket_id: str) -> tuple[list[str], list[str]]:
    svc = TicketDependencyService(session)
    return svc.prerequisites(ticket_id), svc.dependents(ticket_id)


def _dependency_refs(session: Session, ticket_ids: list[str]) -> list[TicketDependencyRef]:
    refs: list[TicketDependencyRef] = []
    for tid in ticket_ids:
        dep = session.get(Ticket, tid)
        if dep is None:
            continue
        ws = workspace_for(session, dep.workspace_id)
        refs.append(
            TicketDependencyRef(
                id=dep.id,
                external_id=dep.external_id,
                title=dep.title,
                state=dep.state,
                work_item_type=dep.work_item_type,
                is_integration_review=dep.is_integration_review,
                workspace_slug=ws.slug if ws else "",
            )
        )
    return refs


def _prerequisite_refs(
    session: Session, ticket: Ticket, ticket_ids: list[str]
) -> list[TicketDependencyRef]:
    """The prerequisites, each saying whether and why it still holds the ticket.

    Judged the way a dispatch judges them (`dependency_hold`), git included, so
    "done, not landed" reads here as it holds there.
    """
    refs = _dependency_refs(session, ticket_ids)
    if not refs or ticket.state in RESOLVED_STATES:
        return refs
    unmet_entries = unmet_prerequisites(session, ticket)
    unmet = {entry.ticket.id: entry for entry in unmet_entries}
    holding = {entry.ticket.id for entry in still_holding(session, ticket, unmet_entries)}
    for ref in refs:
        entry = unmet.get(ref.id)
        if entry is None:
            continue
        ref.unmet_reason = entry.reason.value
        ref.landing_target = entry.target
        ref.overridden = ref.id not in holding
    return refs


def ticket_detail(session: Session, ticket: Ticket) -> TicketDetail:
    """The ticket page's payload. A read: it writes nothing.

    It used to repair on the way, reconciling the ticket with its workflow and
    storing a missing diff or test artifact. The stages and cursor it shows are
    derived from the workflow without writing (`stage_views_for_read`), so the
    repair changed nothing on screen: on live, 329 of 3,371 tickets would have
    been rewritten by a load and none would have read differently. Parents are
    repaired by the sweep (`reconcile_all_parents`).
    """
    orch = OrchestrationService(session)
    summary = ticket_summary(session, ticket)
    template = orch.get_template_for_ticket(ticket)
    transitions = normalize_transitions(resolve_ticket_transitions(session, ticket))
    posture = resolve_compatibility_posture(session, ticket)
    prereq_ids, dependent_ids = _dependency_edge_ids(session, ticket.id)
    criteria = load_criteria(ticket.acceptance_criteria_json)
    return TicketDetail(
        **summary.model_dump(),
        description=ticket.description,
        acceptance_criteria=criteria,
        checked_acceptance_criteria=load_checked(ticket.checked_criteria_json, criteria),
        out_of_scope=load_criteria(ticket.out_of_scope_json),
        dependencies=_prerequisite_refs(session, ticket, prereq_ids),
        dependents=_dependency_refs(session, dependent_ids),
        related=_dependency_refs(session, TicketRelationService(session).related(ticket.id)),
        revision=ticket.revision,
        last_updated_by=ticket.last_updated_by,
        next_status=ticket.next_status,
        blocking_issues=normalize_timeout_stderr(ticket.blocking_issues),
        block_message=normalize_timeout_stderr(block_message_for(session, ticket)),
        state_locked=ticket.state_locked,
        workflow_template_slug=template.slug if template else "",
        workflow_template_name=template.name if template else "",
        workflow_transitions=[WorkflowTransitionView.model_validate(item) for item in transitions],
        artifacts=artifacts_grouped(session, ticket),
        orchestration_runtime=get_ticket_orchestration_runtime(ticket),
        compatibility_posture=ticket.compatibility_posture,
        resolved_compatibility_posture=posture.posture.value,
        compatibility_posture_source=posture.source,
        split=ticket_split(session, ticket),
        active_orchestration=_active_orchestration_view(session, ticket),
        ships_with=ships_with_branch(session, ticket),
        delivery=delivery_of(session, ticket),
    )
