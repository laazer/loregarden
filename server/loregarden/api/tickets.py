import logging

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from fastapi.responses import JSONResponse
from loregarden.api.operation_bodies import OperationBody
from loregarden.api.operation_routes import run_rest_operation
from loregarden.core.workflow_loader import stage_display_name
from loregarden.db.session import get_session
from loregarden.models.domain import (
    AdvanceStageRequest,
    AgentRun,
    BlockedOn,
    FinalizeHierarchyRequest,
    FinalizeHierarchyResponse,
    OrchestrationRun,
    OrchestrationRunStatus,
    RouteWorkflowRequest,
    RunStatus,
    StartOrchestrationRequest,
    StartRunRequest,
    Ticket,
    TicketDetail,
    TicketImportPreviewPathsRequest,
    TicketImportPreviewRequest,
    TicketImportRequest,
    TicketImportResult,
    TicketState,
    TicketStatusSummary,
    TicketSummary,
    TicketTreeNode,
    WorkItemType,
)
from loregarden.services import artifact_service
from loregarden.services.blocked_on import (
    blocked_on_with_retry,
    prerequisite_refs_map,
)
from loregarden.services.dependency_hold import (
    DependencyHold,
    admit_named_start,
)
from loregarden.services.hierarchy_service import build_tree
from loregarden.services.initiative_coverage import covered_ticket_ids, members_among
from loregarden.services.operations import OperationId
from loregarden.services.orchestration import OrchestrationService
from loregarden.services.orchestration_callbacks import OrchestrationCallbackService
from loregarden.services.path_browser import read_import_files
from loregarden.services.queue_admission import QueueAdmissionService
from loregarden.services.run_cancellation import request_cancel, request_orchestration_cancel
from loregarden.services.run_ledger import capacity_waits, failed_run_error_tails, ledger_payload
from loregarden.services.run_service import RunService, schedule_agent_run, schedule_orchestration
from loregarden.services.stage_retry_budget import (
    StageRetryBudgetExceeded,
)
from loregarden.services.ticket_activity import (
    summarize_ticket_status,
)
from loregarden.services.ticket_detail_view import (
    ticket_detail,
)
from loregarden.services.ticket_ids import resolve as resolve_external_id
from loregarden.services.ticket_import_service import TicketImportService
from loregarden.services.ticket_service import TicketService
from loregarden.services.ticket_summaries import apply_ticket_query_filters, workspace_scope
from loregarden.services.workflow_reassignment import describe_reassignment
from loregarden.services.workflow_service import (
    prime_workflow_instances,
    stage_resolution_memo,
)
from sqlmodel import Session, col, select

# Bound, not ImportFrom: py_organization rejects importing a `_` name, and this
# ticket forbids promoting `_test_artifact_is_valid` in artifact_service.
_test_artifact_is_valid = artifact_service._test_artifact_is_valid

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/tickets", tags=["tickets"])


def _collect_ancestors(session: Session, tickets: list[Ticket]) -> list[Ticket]:
    """Walk up each ticket's parent chain.

    build_tree re-parents a ticket to the root when its parent is absent, so a match whose
    ancestors were filtered out would surface as a root node instead of under its hierarchy.
    Every ancestor returned here anchors at least one matching ticket, so the caller needs no
    pruning pass.
    """
    matched_ids = {t.id for t in tickets}
    ancestors: dict[str, Ticket] = {}
    for ticket in tickets:
        parent_id = ticket.parent_ticket_id
        # Stop on a ticket already matched (it walks its own chain) or already collected.
        while parent_id and parent_id not in matched_ids and parent_id not in ancestors:
            parent = session.get(Ticket, parent_id)
            if not parent:
                break
            ancestors[parent.id] = parent
            parent_id = parent.parent_ticket_id
    return list(ancestors.values())


def _build_stage_names(session: Session, tickets: list[Ticket]) -> dict[str, str]:
    stage_names: dict[str, str] = {}
    orch = OrchestrationService(session)
    for t in tickets:
        template = orch.get_template_for_ticket(t)
        if template and t.workflow_stage_key:
            stage_names[t.id] = stage_display_name(template, t.workflow_stage_key)
    return stage_names


@router.get("/tree", response_model=list[TicketTreeNode])
def ticket_tree(
    *,
    workspace: str | None = None,
    state: list[TicketState] | None = Query(default=None),
    work_item_type: list[WorkItemType] | None = Query(default=None),
    blocked_on: list[BlockedOn] | None = Query(default=None),
    milestone: str | None = None,
    search: str | None = None,
    include_members: bool = False,
    ancestor_ticket_id: str | None = None,
    session: Session = Depends(get_session),
) -> list[TicketTreeNode]:
    """The forest of matching tickets, anchored under their ancestors.

    ``include_members`` also lists each initiative's members under it (see
    `build_tree`), and brings in any initiative tracking a listed ticket:
    initiatives bind to no workspace, so a workspace filter never matches one.

    ``ancestor_ticket_id`` keeps only what sits below that ticket (an
    initiative's members included), the same filter `GET /api/tickets` takes.
    The ticket itself comes back as the anchor its subtree hangs from. The
    Ticket page reads its children this way; it used to build the whole
    workspace's tree every one to five seconds to find them.
    """
    scope = workspace_scope(session, workspace)
    if scope.unknown:
        return []

    query = select(Ticket)
    if scope.workspace:
        query = query.where(Ticket.workspace_id == scope.workspace.id)
    query = apply_ticket_query_filters(
        session,
        query,
        states=state,
        work_item_types=work_item_type,
        milestone=milestone,
        search=search,
        blocked_on=blocked_on,
    )
    if ancestor_ticket_id:
        query = query.where(col(Ticket.id).in_(covered_ticket_ids(session, ancestor_ticket_id)))

    tickets = list(session.exec(query).all())
    if not tickets:
        return []

    # Ancestors keep matching tickets anchored in the hierarchy even when the filter excludes them.
    all_tickets = tickets + _collect_ancestors(session, tickets)
    members = members_among(session, [t.id for t in all_tickets]) if include_members else {}
    listed = {t.id for t in all_tickets}
    all_tickets += [
        initiative
        for initiative_id in members
        if initiative_id not in listed and (initiative := session.get(Ticket, initiative_id))
    ]
    with stage_resolution_memo():
        prime_workflow_instances(session, [t.id for t in all_tickets])
        stage_names = _build_stage_names(session, all_tickets)
    answers, retry = blocked_on_with_retry(session, all_tickets)
    return build_tree(
        session,
        all_tickets,
        stage_names=stage_names,
        members=members,
        blocked_on=answers,
        agent_retry=retry,
        waiting_on=prerequisite_refs_map(session, all_tickets),
    )


@router.get("", response_model=list[TicketSummary])
def list_tickets(
    *,
    workspace: str | None = None,
    state: list[TicketState] | None = Query(default=None),
    work_item_type: list[WorkItemType] | None = Query(default=None),
    parent_ticket_id: str | None = None,
    ancestor_ticket_id: str | None = None,
    roots_only: bool = False,
    blocked_on: list[BlockedOn] | None = Query(default=None),
    milestone: str | None = None,
    search: str | None = None,
    limit: int | None = Query(default=None, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    session: Session = Depends(get_session),
) -> list[TicketSummary]:
    """The board's ticket list; see `services.ticket_summaries`."""
    return run_rest_operation(
        session,
        OperationId.TICKET_SUMMARIES,
        {
            "workspace": workspace,
            "states": state,
            "work_item_types": work_item_type,
            "parent_ticket_id": parent_ticket_id,
            "ancestor_ticket_id": ancestor_ticket_id,
            "roots_only": roots_only,
            "blocked_on": blocked_on,
            "milestone": milestone,
            "search": search,
            "limit": limit,
            "offset": offset,
        },
    )


@router.get("/status-summary", response_model=TicketStatusSummary)
def ticket_status_summary(
    *,
    workspace: str | None = None,
    session: Session = Depends(get_session),
) -> TicketStatusSummary:
    """Counts by state alongside what is actually executing.

    Separate from `list_tickets` because the board wants the whole workspace's
    totals, not a page of rows it would have to count client-side.
    """
    scope = workspace_scope(session, workspace)
    if scope.unknown:
        return TicketStatusSummary()
    workspace_id = scope.workspace.id if scope.workspace else None
    return summarize_ticket_status(session, workspace_id=workspace_id)


class TicketCreate(
    OperationBody,
    operation=OperationId.TICKET_CREATE,
    required={"title"},
    # The page names the parent `parent_ticket_id`, and sends null for none.
    renamed={"parent": "parent_ticket_id"},
    nullable={"parent"},
    typed={"work_item_type": WorkItemType},
):
    """A new ticket."""


class TicketDependencyRequest(
    OperationBody, operation=OperationId.TICKET_DEPENDENCY_ADD, supplied={"ticket_id"}
):
    """Add a "this ticket waits for depends_on" edge; accepts a UUID or external_id."""


class TicketRelationRequest(
    OperationBody, operation=OperationId.TICKET_RELATION_ADD, supplied={"ticket_id"}
):
    """Relate this ticket to another (symmetric, non-blocking); accepts a UUID or
    external_id."""


class TicketPatchRequest(
    OperationBody,
    operation=OperationId.TICKET_UPDATE,
    supplied={"ticket_id"},
    # How MCP's criteria combine with the stored list; the page sends the whole list.
    hidden={"mode"},
    renamed={"parent": "parent_ticket_id"},
    typed={"state": TicketState},
    # A PATCH answering 200 to a write it silently discarded is what this refuses.
    extra="forbid",
):
    """An edit to one ticket. `None` is "not supplied" throughout; `""` detaches a
    parent or clears an override."""


@router.post("", response_model=TicketDetail, status_code=201)
def create_ticket(body: TicketCreate, session: Session = Depends(get_session)) -> TicketDetail:
    return run_rest_operation(session, OperationId.TICKET_CREATE, body.model_dump())


@router.post("/import/preview")
def preview_ticket_import(
    body: TicketImportPreviewRequest,
    session: Session = Depends(get_session),
):
    if not body.files:
        raise HTTPException(400, "At least one file is required")
    svc = TicketImportService(session)
    response = svc.preview(
        workspace_slug=body.workspace_slug,
        files=[(file.name, file.content) for file in body.files],
        mode=body.mode or "smart",
    )
    return JSONResponse(content=response.model_dump(exclude_none=True))


@router.post("/import/preview-paths")
def preview_ticket_import_paths(
    body: TicketImportPreviewPathsRequest,
    session: Session = Depends(get_session),
):
    if not body.file_paths:
        raise HTTPException(400, "At least one file path is required")
    try:
        files = read_import_files(body.file_paths)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    svc = TicketImportService(session)
    response = svc.preview(workspace_slug=body.workspace_slug, files=files)
    return JSONResponse(content=response.model_dump(exclude_none=True))


@router.post("/import", response_model=TicketImportResult, status_code=201)
def import_tickets(
    body: TicketImportRequest,
    session: Session = Depends(get_session),
) -> TicketImportResult:
    if not body.tickets:
        raise HTTPException(400, "At least one ticket is required")
    svc = TicketImportService(session)
    result = svc.import_tickets(workspace_slug=body.workspace_slug, tickets=body.tickets)
    if result.errors:
        # Any parent-link / validation failure makes the batch unclean — including
        # partial creates (initiative ok, feature under it rejected). Callers must
        # not treat HTTP 201 as "every row landed".
        raise HTTPException(400, "; ".join(result.errors))
    return result


def _detail_reply(session: Session, ticket_id: str) -> TicketDetail:
    """The detail a write route replies with, built the way the read builds it."""
    ticket = session.get(Ticket, ticket_id) or resolve_external_id(session, ticket_id)
    if ticket is None:
        raise HTTPException(404, "Ticket not found")
    return ticket_detail(session, ticket)


@router.get("/{ticket_id}", response_model=TicketDetail)
def get_ticket(ticket_id: str, session: Session = Depends(get_session)) -> TicketDetail:
    # The shareable id resolves here as well as the UUID: this is the endpoint a
    # pasted `lor-mcp-gateway-142` reaches, and the response carries the UUID the
    # rest of the API is keyed by.
    return run_rest_operation(session, OperationId.TICKET_DETAIL, {"ticket_id": ticket_id})


@router.post("/{ticket_id}/dependencies", response_model=TicketDetail)
def add_ticket_dependency(
    ticket_id: str,
    body: TicketDependencyRequest,
    session: Session = Depends(get_session),
) -> TicketDetail:
    """Link this ticket to wait for another; both a UUID or an external id."""
    return run_rest_operation(
        session,
        OperationId.TICKET_DEPENDENCY_ADD,
        {**body.model_dump(), "ticket_id": ticket_id},
    )


@router.delete("/{ticket_id}/dependencies/{depends_on_id}", response_model=TicketDetail)
def remove_ticket_dependency(
    ticket_id: str,
    depends_on_id: str,
    session: Session = Depends(get_session),
) -> TicketDetail:
    return run_rest_operation(
        session,
        OperationId.TICKET_DEPENDENCY_REMOVE,
        {"ticket_id": ticket_id, "depends_on": depends_on_id},
    )


@router.post("/{ticket_id}/relations", response_model=TicketDetail)
def add_ticket_relation(
    ticket_id: str,
    body: TicketRelationRequest,
    session: Session = Depends(get_session),
) -> TicketDetail:
    """Relate this ticket to another; both a UUID or an external id.

    Symmetric and non-blocking: both tickets list the other, and neither waits.
    """
    return run_rest_operation(
        session,
        OperationId.TICKET_RELATION_ADD,
        {**body.model_dump(), "ticket_id": ticket_id},
    )


@router.delete("/{ticket_id}/relations/{related_id}", response_model=TicketDetail)
def remove_ticket_relation(
    ticket_id: str,
    related_id: str,
    session: Session = Depends(get_session),
) -> TicketDetail:
    return run_rest_operation(
        session,
        OperationId.TICKET_RELATION_REMOVE,
        {"ticket_id": ticket_id, "related_to": related_id},
    )


@router.get("/{ticket_id}/workflow-reassignment", response_model=dict)
def preview_workflow_reassignment(
    ticket_id: str,
    template_slug: str,
    session: Session = Depends(get_session),
) -> dict:
    """What changing this ticket's workflow would cost, before doing it.

    Read-only. The change itself still goes through PATCH — this only lets the
    caller show what is about to be lost.
    """
    ticket = session.get(Ticket, ticket_id)
    if not ticket:
        raise HTTPException(404, "Ticket not found")
    return describe_reassignment(session, ticket, template_slug)


@router.get("/{ticket_id}/ledger", response_model=dict)
def get_ticket_ledger(
    ticket_id: str,
    limit: int = 200,
    session: Session = Depends(get_session),
) -> dict:
    """What happened to this ticket, in order.

    A higher default limit than the run list: the ledger's whole point is the
    shape over time, and truncating it to the most recent handful would hide
    the loops it exists to show.
    """
    ticket = session.get(Ticket, ticket_id)
    if not ticket:
        raise HTTPException(404, "Ticket not found")
    runs = RunService(session).list_runs(ticket_id=ticket_id, limit=limit)
    # The capacity wait rides in this payload rather than one of its own: the
    # Timeline's error state is written for a single read, not two.
    waits = capacity_waits(session, ticket_id, runs)
    return ledger_payload(runs, failed_run_error_tails(session, runs), capacity_wait=waits)


@router.get("/{ticket_id}/artifacts", response_model=dict)
def get_ticket_artifacts(
    ticket_id: str,
    limit: int = 200,
    session: Session = Depends(get_session),
) -> dict:
    """Raw artifact feed for this ticket (newest first).

    Unlike the grouped `ticket.artifacts` view, this includes every kind agents
    attach — including ad-hoc kinds that never map to Diff/Logs/Context tabs.
    """
    return run_rest_operation(
        session, OperationId.ARTIFACT_FEED, {"ticket_id": ticket_id, "limit": limit}
    )


@router.patch("/{ticket_id}", response_model=TicketDetail)
def update_ticket(
    ticket_id: str,
    body: TicketPatchRequest,
    session: Session = Depends(get_session),
) -> TicketDetail:
    # `None` is "not supplied" throughout the model, so it is left out; ""
    # stays, since it detaches a parent or clears an override.
    return run_rest_operation(
        session,
        OperationId.TICKET_UPDATE,
        {**body.model_dump(exclude_none=True), "ticket_id": ticket_id},
    )


@router.delete("/{ticket_id}")
def delete_ticket(ticket_id: str, session: Session = Depends(get_session)) -> dict:
    try:
        TicketService(session).delete_ticket(ticket_id)
    except ValueError as exc:
        message = str(exc)
        status_code = 404 if message == "Ticket not found" else 400
        raise HTTPException(status_code, message) from exc
    return {"ok": True}


def _admit_named_start(
    session: Session, ticket: Ticket, stage_key: str | None, ignore_prerequisites: bool
) -> None:
    """409 when the ticket waits on prerequisites and nobody said to start it anyway.

    Checked before admission, so a held start never takes a slot or a queue place.
    """
    try:
        admit_named_start(
            session,
            ticket,
            stage_key=stage_key or ticket.workflow_stage_key,
            ignore_prerequisites=ignore_prerequisites,
            actor="operator",
        )
    except DependencyHold as hold:
        # 409, as for the retry budget: the identical request with
        # ignore_prerequisites=true starts it.
        raise HTTPException(409, str(hold)) from hold


@router.post("/{ticket_id}/orchestrate", response_model=TicketDetail)
def orchestrate_ticket(
    ticket_id: str,
    body: StartOrchestrationRequest = Body(default_factory=StartOrchestrationRequest),
    session: Session = Depends(get_session),
) -> TicketDetail:
    ticket = session.get(Ticket, ticket_id)
    if not ticket:
        raise HTTPException(404, "Ticket not found")
    active = OrchestrationCallbackService(session).get_active_orchestration_run(ticket.id)
    if active:
        raise HTTPException(400, f"Orchestration already running: {active.run_code}")
    _admit_named_start(session, ticket, body.stop_at_stage_key, body.ignore_prerequisites)
    # Through the same slot pool as the queue board. Started directly, this
    # oversubscribed the machine — the board would show idle lanes while every
    # agent the box could run was already busy.
    reservation = QueueAdmissionService(session).reserve_orchestration(
        ticket,
        auto_approve=body.auto_approve,
        approve_design_plans=body.approve_design_plans,
        auto_repair=body.auto_repair,
        stop_at_stage_key=body.stop_at_stage_key,
        preferred_slot=body.slot_number,
        # Only used if the pool is full and this request parks: the entry is
        # the whole record of the ask by the time a lane reaches it.
        driver=body.driver.value if body.driver else "",
        max_stages=body.max_stages,
        timeout_seconds=body.timeout_seconds,
    )
    if not reservation.admitted:
        session.refresh(ticket)
        detail = _detail_reply(session, ticket_id)
        detail.admission = reservation.as_dict()
        return detail

    # Claimed before dispatch, not read back after. `schedule_orchestration`
    # creates the row on a worker thread, so binding by re-reading the ticket's
    # active run raced that thread and lost: the reservation never bound, and a
    # slot naming nothing is a slot no release can ever find.
    callbacks = OrchestrationCallbackService(session)
    claim = callbacks.claim_orchestration_run(
        ticket,
        driver=body.driver,
        auto_approve=body.auto_approve,
        approve_design_plans=body.approve_design_plans,
        auto_repair=body.auto_repair,
        stop_at_stage_key=body.stop_at_stage_key or "",
        timeout_override_seconds=body.timeout_seconds,
    )
    reservation.bind(orchestration_run_id=claim.id)

    try:
        schedule_orchestration(
            ticket.id,
            max_stages=body.max_stages,
            driver=body.driver,
            stop_at_stage_key=body.stop_at_stage_key,
            auto_approve=body.auto_approve,
            approve_design_plans=body.approve_design_plans,
            auto_repair=body.auto_repair,
            timeout_seconds=body.timeout_seconds,
        )
    except ValueError as exc:
        callbacks.abandon_claim(claim, message=str(exc))
        reservation.release()
        raise HTTPException(400, str(exc)) from exc

    session.refresh(ticket)
    return _detail_reply(session, ticket_id)


@router.post("/{ticket_id}/open-pr", response_model=TicketDetail)
def open_ticket_pr(ticket_id: str, session: Session = Depends(get_session)) -> TicketDetail:
    ticket = session.get(Ticket, ticket_id)
    if not ticket:
        raise HTTPException(404, "Ticket not found")
    from loregarden.services.github_pr_service import create_ticket_pull_request

    try:
        create_ticket_pull_request(session, ticket)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return _detail_reply(session, ticket_id)


@router.post("/{ticket_id}/commit-push", response_model=TicketDetail)
def commit_push_ticket(ticket_id: str, session: Session = Depends(get_session)) -> TicketDetail:
    ticket = session.get(Ticket, ticket_id)
    if not ticket:
        raise HTTPException(404, "Ticket not found")
    from loregarden.services.git_commit_push_service import (
        NothingToCommitError,
        commit_and_push_ticket_branch,
    )

    try:
        commit_and_push_ticket_branch(session, ticket)
    except NothingToCommitError as exc:
        raise HTTPException(409, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return _detail_reply(session, ticket_id)


@router.post("/{ticket_id}/start", response_model=TicketDetail)
def start_run(
    ticket_id: str,
    body: StartRunRequest,
    session: Session = Depends(get_session),
) -> TicketDetail:
    ticket = session.get(Ticket, ticket_id)
    if not ticket:
        raise HTTPException(404, "Ticket not found")
    if not body.manual:
        raise HTTPException(
            400,
            "Use POST /orchestrate for default orchestration. Pass manual=true for single-stage debug runs.",
        )
    _admit_named_start(session, ticket, body.stage_key, body.ignore_prerequisites)
    # A debug stage run still costs an agent, so it waits its turn like anything
    # else. Parked as a stage, not an orchestration.
    reservation = QueueAdmissionService(session).reserve_stage(
        ticket,
        stage_key=body.stage_key,
        auto_approve=body.auto_approve,
        preferred_slot=body.slot_number,
        timeout_seconds=body.timeout_seconds,
        force=body.force,
    )
    if not reservation.admitted:
        session.refresh(ticket)
        detail = _detail_reply(session, ticket_id)
        detail.admission = reservation.as_dict()
        return detail

    run_svc = RunService(session)
    try:
        run = run_svc.start_stage_execution(
            ticket,
            stage_key=body.stage_key,
            auto_approve=body.auto_approve,
            timeout_seconds=body.timeout_seconds,
            force=body.force,
        )
    except StageRetryBudgetExceeded as exc:
        reservation.release()
        # 409, not 400: the request is well formed and the stage is startable —
        # it is the ticket's state that refuses, and the caller can retry the
        # identical request with force=true.
        raise HTTPException(409, str(exc)) from exc
    except ValueError as exc:
        reservation.release()
        raise HTTPException(400, str(exc)) from exc
    if run:
        reservation.bind(run_id=run.id)
        schedule_agent_run(run.id)
    else:
        # Nothing started, so the slot must go back rather than be held by a
        # run that does not exist.
        reservation.release()
    session.refresh(ticket)
    return _detail_reply(session, ticket_id)


@router.post("/{ticket_id}/stop", response_model=TicketDetail)
def stop_ticket(ticket_id: str, session: Session = Depends(get_session)) -> TicketDetail:
    """Cancel in-flight agent runs and orchestration for this ticket."""
    ticket = session.get(Ticket, ticket_id)
    if not ticket:
        raise HTTPException(404, "Ticket not found")

    in_flight = session.exec(
        select(AgentRun).where(
            AgentRun.ticket_id == ticket_id,
            AgentRun.status.in_(
                [RunStatus.QUEUED, RunStatus.RUNNING, RunStatus.AWAITING_PERMISSION]
            ),
        )
    ).all()
    for run in in_flight:
        try:
            request_cancel(session, run)
        except ValueError:
            # silent-ok: already finishing / double-stop; the run is stopping anyway,
            # and the refreshed ticket returned below reports its real status.
            continue

    orch_runs = session.exec(
        select(OrchestrationRun).where(
            OrchestrationRun.ticket_id == ticket_id,
            OrchestrationRun.status.in_(
                [OrchestrationRunStatus.QUEUED, OrchestrationRunStatus.RUNNING]
            ),
        )
    ).all()
    callbacks = OrchestrationCallbackService(session)
    for orch_run in orch_runs:
        if orch_run.external_harness:
            # `cancel_requested_at` is polled by BuiltinOrchestrator, which does
            # not drive this run — there is no process here to notice the flag.
            # Setting it and reporting success left the run RUNNING with its
            # lane held, so stop was a silent no-op on exactly the runs whose
            # lane nothing else could release. Settle it here instead.
            callbacks.complete_orchestration(
                orch_run,
                ticket,
                status=OrchestrationRunStatus.CANCELLED,
                message="Stopped by the operator; the external harness owns no process here.",
            )
            continue
        try:
            request_orchestration_cancel(session, orch_run)
        except ValueError:
            # silent-ok: already finishing / double-stop; the refreshed ticket
            # returned below reports the orchestration's real status.
            continue

    session.refresh(ticket)
    return _detail_reply(session, ticket_id)


@router.post("/{ticket_id}/advance", response_model=TicketDetail)
def advance_stage(
    ticket_id: str,
    body: AdvanceStageRequest,
    session: Session = Depends(get_session),
) -> TicketDetail:
    ticket = session.get(Ticket, ticket_id)
    if not ticket:
        raise HTTPException(404, "Ticket not found")
    orch = OrchestrationService(session)
    try:
        orch.advance_stage(ticket)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    session.refresh(ticket)
    return _detail_reply(session, ticket_id)


@router.post("/{ticket_id}/route-workflow", response_model=TicketDetail)
def route_workflow_stage(
    ticket_id: str,
    body: RouteWorkflowRequest,
    session: Session = Depends(get_session),
) -> TicketDetail:
    ticket = session.get(Ticket, ticket_id)
    if not ticket:
        raise HTTPException(404, "Ticket not found")
    orch = OrchestrationService(session)
    try:
        orch.route_workflow_stage(
            ticket,
            from_stage_key=body.from_stage_key,
            outcome=body.outcome,
            next_stage_key=body.next_stage_key,
            next_agent=body.next_agent,
            blocking_issues=body.blocking_issues,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return _detail_reply(session, ticket_id)


@router.post("/finalize-hierarchy", response_model=FinalizeHierarchyResponse, status_code=201)
def finalize_hierarchy(
    body: FinalizeHierarchyRequest, session: Session = Depends(get_session)
) -> FinalizeHierarchyResponse:
    """Create all work items in hierarchy atomically, with parent-child validation."""
    from loregarden.services.finalize_hierarchy import (
        ProposalValidationError,
    )
    from loregarden.services.finalize_hierarchy import (
        finalize_hierarchy as persist_hierarchy,
    )

    try:
        created_ids = persist_hierarchy(
            session,
            workspace_slug=body.workspace_slug,
            hierarchy=body.hierarchy,
        )
    except (ProposalValidationError, ValueError) as e:
        session.rollback()
        raise HTTPException(400, str(e)) from e
    except Exception as e:
        session.rollback()
        raise HTTPException(400, str(e)) from e

    return FinalizeHierarchyResponse(created_ids=created_ids, total_created=len(created_ids))
