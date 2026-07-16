import json

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from fastapi.responses import JSONResponse
from loregarden.agents.cli_adapters import render_terminal_handoff_command
from loregarden.db.session import get_session
from loregarden.models.domain import (
    AdvanceStageRequest,
    Artifact,
    FinalizeHierarchyRequest,
    FinalizeHierarchyResponse,
    RouteWorkflowRequest,
    RunStatus,
    StageStatus,
    StartOrchestrationRequest,
    StartRunRequest,
    Ticket,
    TicketCreate,
    TicketDetail,
    TicketImportPreviewPathsRequest,
    TicketImportPreviewRequest,
    TicketImportRequest,
    TicketImportResult,
    TicketState,
    TicketSummary,
    TicketTreeNode,
    TriageMessageCreate,
    UpdateTicketRequest,
    WorkflowTransitionView,
    WorkItemType,
    Workspace,
    WorkspaceRuntimeSettings,
    WorkspaceRuntimeUpdate,
)
from loregarden.services.cli_settings import (
    get_ticket_orchestration_runtime,
    set_ticket_orchestration_runtime,
)
from loregarden.services.hierarchy_service import build_tree, child_count
from loregarden.services.orchestration import OrchestrationService
from loregarden.services.orchestration_callbacks import OrchestrationCallbackService
from loregarden.services.path_browser import read_import_files
from loregarden.services.run_errors import normalize_timeout_stderr
from loregarden.services.run_service import RunService, schedule_agent_run, schedule_orchestration
from loregarden.services.ticket_import_service import TicketImportService
from loregarden.services.ticket_service import TicketService
from loregarden.services.triage_run_service import (
    TriageConflictError,
    schedule_triage_turn,
    start_triage_run,
)
from loregarden.services.triage_service import set_triage_runtime, triage_snapshot
from sqlmodel import Session, col, select

router = APIRouter(prefix="/tickets", tags=["tickets"])


def _latest_run_code(session: Session, ticket_id: str) -> str:
    from loregarden.models.domain import AgentRun

    run = session.exec(
        select(AgentRun).where(AgentRun.ticket_id == ticket_id).order_by(AgentRun.created_at.desc())
    ).first()
    return run.run_code if run else ""


def _ticket_summary(session: Session, ticket: Ticket) -> TicketSummary:
    ws = session.get(Workspace, ticket.workspace_id)
    orch = OrchestrationService(session)
    template = orch.get_template_for_ticket(ticket)
    stage_name = ""
    if template and ticket.workflow_stage_key:
        from loregarden.core.workflow_loader import stage_display_name

        stage_name = stage_display_name(template, ticket.workflow_stage_key)
    stages = orch.build_stage_views(ticket)
    session.refresh(ticket)
    return TicketSummary(
        id=ticket.id,
        external_id=ticket.external_id,
        title=ticket.title,
        state=ticket.state,
        priority=ticket.priority,
        workspace_slug=ws.slug if ws else "",
        workflow_stage_key=ticket.workflow_stage_key,
        workflow_stage_status=ticket.workflow_stage_status,
        workflow_stage_name=stage_name,
        run_code=_latest_run_code(session, ticket.id),
        work_item_type=ticket.work_item_type,
        parent_ticket_id=ticket.parent_ticket_id,
        milestone=ticket.milestone,
        branch=ticket.branch,
        child_count=child_count(session, ticket.id),
        next_agent=ticket.next_agent,
        stages=stages,
    )


def _artifacts_grouped(session: Session, ticket: Ticket) -> dict:
    from loregarden.models.domain import AgentRun, Workspace
    from loregarden.services.artifact_service import (
        _diff_artifact_is_valid,
        _test_artifact_is_valid,
        ensure_diff_artifact,
        ensure_test_artifact,
    )

    ticket_id = ticket.id
    grouped: dict = {
        "diff": None,
        "logs": [],
        "tests": None,
        "context": [],
        "live": None,
        "error": None,
        "pr": None,
    }
    artifacts = session.exec(select(Artifact).where(Artifact.ticket_id == ticket_id)).all()
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
            grouped["context"].append(content)
        elif art.kind == "pr":
            grouped["pr"] = content
    if error_artifacts:
        latest_error = sorted(error_artifacts, key=lambda a: -a.created_at.timestamp())[0]
        error_content = json.loads(latest_error.content_json or "{}")
        message = error_content.get("message")
        if isinstance(message, str):
            error_content["message"] = normalize_timeout_stderr(message)
        grouped["error"] = error_content
    if log_artifacts:
        active_run = session.exec(
            select(AgentRun)
            .where(AgentRun.ticket_id == ticket_id, AgentRun.status == RunStatus.RUNNING)
            .order_by(AgentRun.created_at.desc())
        ).first()
        if not active_run:
            active_run = session.exec(
                select(AgentRun)
                .where(
                    AgentRun.ticket_id == ticket_id,
                    AgentRun.status == RunStatus.AWAITING_PERMISSION,
                )
                .order_by(AgentRun.created_at.desc())
            ).first()

        best: Artifact | None = None
        if active_run:
            best = next((art for art in log_artifacts if art.run_id == active_run.id), None)
            if not best:
                grouped["logs"] = []
                if active_run.status == RunStatus.AWAITING_PERMISSION:
                    grouped["live"] = "Awaiting your approval in Triage or Inbox…"
                else:
                    grouped["live"] = "Agent running…"
                return grouped

        if not best:
            active_log_statuses = {RunStatus.RUNNING, RunStatus.AWAITING_PERMISSION}

            def _log_sort_key(item: Artifact) -> tuple:
                body = json.loads(item.content_json or "{}")
                run = session.get(AgentRun, item.run_id) if item.run_id else None
                is_live = bool(body.get("live")) and run and run.status in active_log_statuses
                live_rank = 0 if is_live else 1
                return (live_rank, -item.created_at.timestamp())

            best = sorted(log_artifacts, key=_log_sort_key)[0]

        content = json.loads(best.content_json or "{}")
        grouped["logs"] = content.get("lines", [])
        live = content.get("live")
        run = session.get(AgentRun, best.run_id) if best.run_id else None
        if live and run and run.status not in {RunStatus.RUNNING, RunStatus.AWAITING_PERMISSION}:
            live = None
        grouped["live"] = live

    workspace = session.get(Workspace, ticket.workspace_id)
    if workspace:
        if not grouped["diff"] or not _diff_artifact_is_valid(grouped["diff"] or {}):
            grouped["diff"] = ensure_diff_artifact(session, ticket=ticket, workspace=workspace)
        if not grouped["tests"] or not _test_artifact_is_valid(grouped["tests"] or {}):
            grouped["tests"] = ensure_test_artifact(session, ticket=ticket)
    if not grouped.get("live") and ticket.workflow_stage_status == StageStatus.AWAITING:
        grouped["live"] = "Awaiting your approval in Triage or Inbox…"
    return grouped


def _workspace_filter(session: Session, workspace: str | None):
    if not workspace:
        return None
    ws = session.exec(select(Workspace).where(Workspace.slug == workspace)).first()
    if not ws:
        return False
    return ws


def _apply_ticket_query_filters(
    query,
    *,
    states: list[TicketState] | None,
    work_item_types: list[WorkItemType] | None,
    milestone: str | None,
    search: str | None,
):
    if states:
        query = query.where(col(Ticket.state).in_(states))
    if work_item_types:
        query = query.where(col(Ticket.work_item_type).in_(work_item_types))
    if milestone:
        query = query.where(Ticket.milestone == milestone)
    if search:
        term = f"%{search.strip()}%"
        query = query.where((col(Ticket.title).like(term)) | (col(Ticket.external_id).like(term)))
    return query


def _collect_ancestors_for_tree(session: Session, parent_tickets: list[Ticket], all_tickets_ids: set[str]) -> list[Ticket]:
    """Recursively collect ancestors of parent tickets for full hierarchy."""
    ancestors_to_include: set[str] = set()
    for parent in parent_tickets:
        current = parent
        while current.parent_ticket_id:
            ancestor_id = current.parent_ticket_id
            if ancestor_id in ancestors_to_include or ancestor_id in all_tickets_ids:
                break
            ancestors_to_include.add(ancestor_id)
            ancestor = session.get(Ticket, ancestor_id)
            if not ancestor:
                break
            current = ancestor
    if ancestors_to_include:
        return session.exec(select(Ticket).where(Ticket.id.in_(ancestors_to_include))).all()
    return []


@router.get("/tree", response_model=list[TicketTreeNode])
def ticket_tree(
    *,
    workspace: str | None = None,
    state: list[TicketState] | None = Query(default=None),
    work_item_type: list[WorkItemType] | None = Query(default=None),
    milestone: str | None = None,
    search: str | None = None,
    session: Session = Depends(get_session),
) -> list[TicketTreeNode]:
    ws = _workspace_filter(session, workspace)
    if ws is False:
        return []

    query = select(Ticket)
    if ws:
        query = query.where(Ticket.workspace_id == ws.id)
    query = _apply_ticket_query_filters(
        query,
        states=state,
        work_item_types=work_item_type,
        milestone=milestone,
        search=search,
    )

    tickets = session.exec(query).all()
    filtered_ids = {t.id for t in tickets}

    # Collect immediate parents of filtered tickets
    parents_to_include = {t.parent_ticket_id for t in tickets if t.parent_ticket_id}
    parent_ids_to_fetch = parents_to_include - filtered_ids
    parent_tickets = []
    if parent_ids_to_fetch:
        parent_tickets = session.exec(select(Ticket).where(Ticket.id.in_(parent_ids_to_fetch))).all()

    # Collect ancestors and build complete ticket list
    all_tickets = list(tickets) + parent_tickets
    ancestor_tickets = _collect_ancestors_for_tree(session, parent_tickets, {t.id for t in all_tickets})
    all_tickets.extend(ancestor_tickets)

    # Build stage names for display
    stage_names: dict[str, str] = {}
    orch = OrchestrationService(session)
    for t in all_tickets:
        template = orch.get_template_for_ticket(t)
        if template and t.workflow_stage_key:
            from loregarden.core.workflow_loader import stage_display_name

            stage_names[t.id] = stage_display_name(template, t.workflow_stage_key)

    tree = build_tree(session, all_tickets, stage_names=stage_names)

    # Prune nodes: keep if matches filter or has any surviving descendants
    def prune_node(node: TicketTreeNode) -> TicketTreeNode | None:
        # Recursively prune children first
        pruned_children = []
        for child in node.children:
            pruned_child = prune_node(child)
            if pruned_child:
                pruned_children.append(pruned_child)

        # Keep node if it matches the filter or has any pruned children
        if node.id in filtered_ids or pruned_children:
            return TicketTreeNode(
                id=node.id,
                external_id=node.external_id,
                title=node.title,
                state=node.state,
                priority=node.priority,
                work_item_type=node.work_item_type,
                workspace_slug=node.workspace_slug,
                workflow_stage_name=node.workflow_stage_name,
                workflow_stage_status=node.workflow_stage_status,
                child_count=len(pruned_children),
                children=pruned_children,
            )
        return None

    pruned_tree = []
    for node in tree:
        pruned_node = prune_node(node)
        if pruned_node:
            pruned_tree.append(pruned_node)

    # Debug
    if work_item_type and len(pruned_tree) < len(tree):
        import sys
        print(f"DEBUG: Pruned {len(tree) - len(pruned_tree)} root nodes", file=sys.stderr)
        print(f"DEBUG: filtered_ids count: {len(filtered_ids)}", file=sys.stderr)
        for node in tree:
            if node not in pruned_tree:
                direct_matching = [c for c in node.children if c.id in filtered_ids]
                print(f"DEBUG: Removed {node.external_id}, direct matching children: {len(direct_matching)}", file=sys.stderr)

    return pruned_tree


@router.get("", response_model=list[TicketSummary])
def list_tickets(
    *,
    workspace: str | None = None,
    state: list[TicketState] | None = Query(default=None),
    work_item_type: list[WorkItemType] | None = Query(default=None),
    parent_ticket_id: str | None = None,
    roots_only: bool = False,
    milestone: str | None = None,
    search: str | None = None,
    session: Session = Depends(get_session),
) -> list[TicketSummary]:
    ws = _workspace_filter(session, workspace)
    if ws is False:
        return []

    query = select(Ticket)
    if ws:
        query = query.where(Ticket.workspace_id == ws.id)
    query = _apply_ticket_query_filters(
        query,
        states=state,
        work_item_types=work_item_type,
        milestone=milestone,
        search=search,
    )
    if parent_ticket_id:
        query = query.where(Ticket.parent_ticket_id == parent_ticket_id)
    if roots_only:
        query = query.where(Ticket.parent_ticket_id.is_(None))  # type: ignore[union-attr]
    tickets = session.exec(query.order_by(Ticket.priority, Ticket.created_at)).all()
    return [_ticket_summary(session, t) for t in tickets]


@router.post("", response_model=TicketDetail, status_code=201)
def create_ticket(body: TicketCreate, session: Session = Depends(get_session)) -> TicketDetail:
    svc = TicketService(session)
    try:
        ticket = svc.create_ticket(
            workspace_slug=body.workspace_slug,
            title=body.title,
            work_item_type=body.work_item_type,
            parent_ticket_id=body.parent_ticket_id,
            description=body.description,
            acceptance_criteria=body.acceptance_criteria,
            priority=body.priority,
            milestone=body.milestone,
            external_id=body.external_id,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return get_ticket(ticket.id, session)


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
    if result.created_count == 0 and result.errors:
        raise HTTPException(400, "; ".join(result.errors))
    return result


@router.get("/{ticket_id}/triage")
def get_ticket_triage(ticket_id: str, session: Session = Depends(get_session)) -> dict:
    ticket = session.get(Ticket, ticket_id)
    if not ticket:
        raise HTTPException(404, "Ticket not found")
    return triage_snapshot(session, ticket)


@router.post("/{ticket_id}/triage/messages", status_code=202)
def post_triage_message(
    ticket_id: str,
    body: TriageMessageCreate,
    session: Session = Depends(get_session),
) -> dict:
    ticket = session.get(Ticket, ticket_id)
    if not ticket:
        raise HTTPException(404, "Ticket not found")
    try:
        user_message, run = start_triage_run(
            session, ticket, body.content, auto_approve=body.auto_approve
        )
    except TriageConflictError as exc:
        raise HTTPException(409, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    schedule_triage_turn(run.id)
    return {
        "user_message": {
            "id": user_message.id,
            "role": user_message.role,
            "content": user_message.content,
            "created_at": user_message.created_at.isoformat(),
        },
        "run_id": run.id,
        "status": "queued",
    }


@router.patch("/{ticket_id}/triage/runtime", response_model=WorkspaceRuntimeSettings)
def patch_triage_runtime(
    ticket_id: str,
    body: WorkspaceRuntimeUpdate,
    session: Session = Depends(get_session),
) -> WorkspaceRuntimeSettings:
    ticket = session.get(Ticket, ticket_id)
    if not ticket:
        raise HTTPException(404, "Ticket not found")
    try:
        return set_triage_runtime(session, ticket, body)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.patch("/{ticket_id}/runtime", response_model=WorkspaceRuntimeSettings)
def patch_ticket_runtime(
    ticket_id: str,
    body: WorkspaceRuntimeUpdate,
    session: Session = Depends(get_session),
) -> WorkspaceRuntimeSettings:
    ticket = session.get(Ticket, ticket_id)
    if not ticket:
        raise HTTPException(404, "Ticket not found")
    try:
        return set_ticket_orchestration_runtime(session, ticket, body)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/{ticket_id}", response_model=TicketDetail)
def get_ticket(ticket_id: str, session: Session = Depends(get_session)) -> TicketDetail:
    ticket = session.get(Ticket, ticket_id)
    if not ticket:
        raise HTTPException(404, "Ticket not found")
    orch = OrchestrationService(session)
    orch.reconcile_ticket(ticket)
    session.refresh(ticket)
    summary = _ticket_summary(session, ticket)
    template = orch.get_template_for_ticket(ticket)
    from loregarden.services.workflow_routing import normalize_transitions_for_api

    transitions = normalize_transitions_for_api(template.transitions_json) if template else []
    return TicketDetail(
        **summary.model_dump(),
        description=ticket.description,
        acceptance_criteria=json.loads(ticket.acceptance_criteria_json or "[]"),
        revision=ticket.revision,
        last_updated_by=ticket.last_updated_by,
        next_status=ticket.next_status,
        blocking_issues=normalize_timeout_stderr(ticket.blocking_issues),
        state_locked=ticket.state_locked,
        workflow_template_slug=template.slug if template else "",
        workflow_template_name=template.name if template else "",
        workflow_transitions=[WorkflowTransitionView.model_validate(item) for item in transitions],
        artifacts=_artifacts_grouped(session, ticket),
        orchestration_runtime=get_ticket_orchestration_runtime(ticket),
    )


@router.patch("/{ticket_id}", response_model=TicketDetail)
def update_ticket(
    ticket_id: str,
    body: UpdateTicketRequest,
    session: Session = Depends(get_session),
) -> TicketDetail:
    ticket = session.get(Ticket, ticket_id)
    if not ticket:
        raise HTTPException(404, "Ticket not found")
    orch = OrchestrationService(session)
    try:
        orch.update_ticket_manual(ticket, body)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    session.refresh(ticket)
    return get_ticket(ticket_id, session)


@router.delete("/{ticket_id}")
def delete_ticket(ticket_id: str, session: Session = Depends(get_session)) -> dict:
    try:
        TicketService(session).delete_ticket(ticket_id)
    except ValueError as exc:
        message = str(exc)
        status_code = 404 if message == "Ticket not found" else 400
        raise HTTPException(status_code, message) from exc
    return {"ok": True}


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
    try:
        schedule_orchestration(
            ticket.id,
            max_stages=body.max_stages,
            driver=body.driver,
            stop_at_stage_key=body.stop_at_stage_key,
            auto_approve=body.auto_approve,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    session.refresh(ticket)
    return get_ticket(ticket_id, session)


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
    return get_ticket(ticket_id, session)


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
    return get_ticket(ticket_id, session)


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
    run_svc = RunService(session)
    try:
        run = run_svc.start_stage_execution(
            ticket,
            stage_key=body.stage_key,
            auto_approve=body.auto_approve,
            timeout_seconds=body.timeout_seconds,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    if run:
        schedule_agent_run(run.id)
    session.refresh(ticket)
    return get_ticket(ticket_id, session)


@router.post("/{ticket_id}/terminal_handoff_command")
def build_terminal_handoff_command(
    ticket_id: str,
    body: StartRunRequest,
    session: Session = Depends(get_session),
) -> dict:
    """Prepare a stage run for a human to execute in their own terminal.

    Creates the same AgentRun/workflow-running state as POST /start, but returns a
    ready-to-paste CLI command instead of spawning the agent as a child of this process —
    so the run survives an app server restart.
    """
    ticket = session.get(Ticket, ticket_id)
    if not ticket:
        raise HTTPException(404, "Ticket not found")
    run_svc = RunService(session)
    try:
        run = run_svc.start_stage_execution(
            ticket,
            stage_key=body.stage_key,
            auto_approve=body.auto_approve,
            timeout_seconds=body.timeout_seconds,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    if not run:
        raise HTTPException(400, "This stage does not run a CLI agent")
    try:
        invocation, cleanup_path = run_svc.executor.prepare_terminal_handoff(run, ticket)
        command = render_terminal_handoff_command(invocation, cleanup_path=cleanup_path)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"run_id": run.id, "adapter": invocation.adapter, "command": command}


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
    return get_ticket(ticket_id, session)


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
    return get_ticket(ticket_id, session)


@router.post("/finalize-hierarchy", response_model=FinalizeHierarchyResponse, status_code=201)
def finalize_hierarchy(
    body: FinalizeHierarchyRequest, session: Session = Depends(get_session)
) -> FinalizeHierarchyResponse:
    """Create all work items in hierarchy atomically, with parent-child validation."""
    from loregarden.services.hierarchy_service import validate_parent_child
    from loregarden.services.proposal_validator import ProposalValidationError, ProposalValidator

    ws = session.exec(select(Workspace).where(Workspace.slug == body.workspace_slug)).first()
    if not ws:
        raise HTTPException(400, f"Workspace not found: {body.workspace_slug}")

    if not body.hierarchy:
        return FinalizeHierarchyResponse(created_ids=[], total_created=0)

    created_ids: list[str] = []

    try:
        # Validate and normalize proposal using comprehensive validator
        try:
            validated_hierarchy = ProposalValidator.validate_all(body.hierarchy)
        except ProposalValidationError as e:
            raise HTTPException(400, f"Proposal validation failed: {e}") from e

        def collect_all_items(items: list) -> list:
            """Collect all items in parent-first order."""
            result = []
            for item in items:
                result.append(item)
                if item.children:
                    result.extend(collect_all_items(item.children))
            return result

        all_items = collect_all_items(validated_hierarchy)

        external_ids_in_hierarchy = set()
        for item in all_items:
            ext_id = item.external_id.strip()
            if ext_id in external_ids_in_hierarchy:
                raise ValueError(f"Duplicate external_id in hierarchy: {ext_id}")
            external_ids_in_hierarchy.add(ext_id)

        existing_ids = {
            t.external_id
            for t in session.exec(select(Ticket).where(Ticket.workspace_id == ws.id)).all()
        }

        for ext_id in external_ids_in_hierarchy:
            if ext_id in existing_ids:
                raise ValueError(f"external_id already exists in workspace: {ext_id}")

        id_mapping = {}

        def create_item_recursive(item, parent_id: str | None = None):
            """Create item and all children recursively, tracking IDs in creation order."""
            title = item.title.strip()
            if not title:
                raise ValueError("Title is required")

            if item.priority < 1 or item.priority > 3:
                raise ValueError(f"Invalid priority {item.priority}: must be in [1, 3]")

            if item.work_item_type == WorkItemType.MILESTONE:
                if item.parent_ticket_id:
                    raise ValueError("Milestones cannot have a parent")
                parent_id = None
            elif parent_id:
                parent = session.get(Ticket, parent_id)
                if not parent:
                    raise ValueError(f"Parent not found: {parent_id}")
                validate_parent_child(parent.work_item_type, item.work_item_type)
            else:
                if item.work_item_type not in (
                    WorkItemType.MILESTONE,
                    WorkItemType.FEATURE,
                    WorkItemType.CAPABILITY,
                ):
                    raise ValueError(f"{item.work_item_type.value} cannot be a root item")

            ext_id = item.external_id.strip()
            if not ext_id:
                raise ValueError("external_id is required")

            new_ticket = Ticket(
                external_id=ext_id,
                workspace_id=ws.id,
                title=title,
                description=item.description.strip() if item.description else "",
                state=TicketState.BACKLOG,
                priority=item.priority,
                work_item_type=item.work_item_type,
                parent_ticket_id=parent_id,
                acceptance_criteria_json=json.dumps(
                    [line.strip() for line in (item.acceptance_criteria or []) if line.strip()]
                ),
                last_updated_by="system",
            )
            session.add(new_ticket)
            session.flush()

            ticket_id = new_ticket.id
            created_ids.append(ticket_id)
            id_mapping[ext_id] = ticket_id

            for child_item in item.children:
                create_item_recursive(child_item, parent_id=ticket_id)

        for item in validated_hierarchy:
            create_item_recursive(item, parent_id=None)

        session.commit()

    except (ValueError, Exception) as e:
        session.rollback()
        error_msg = str(e)
        raise HTTPException(400, error_msg) from e

    return FinalizeHierarchyResponse(created_ids=created_ids, total_created=len(created_ids))
