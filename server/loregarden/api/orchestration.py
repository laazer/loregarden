"""REST API for orchestration callbacks — used by MCP server and external drivers."""

from fastapi import APIRouter, Body, Depends, HTTPException
from loregarden.db.session import get_session
from loregarden.models.domain import (
    AttachArtifactRequest,
    BlockTicketRequest,
    CompleteOrchestrationRequest,
    CompleteStageRequest,
    GatesConfigUpdate,
    GitAutomationView,
    OrchestrationDriver,
    OrchestrationProfileView,
    OrchestrationRun,
    OrchestrationRunView,
    RequestApprovalRequest,
    SkipStageRequest,
    StartOrchestrationRequest,
    StartStageRequest,
    Workspace,
)
from loregarden.services.builtin_orchestrator import BuiltinOrchestrator
from loregarden.services.gate_runner import gates_can_run
from loregarden.services.orchestration import OrchestrationService
from loregarden.services.orchestration_callbacks import OrchestrationCallbackService
from loregarden.services.orchestration_profile import (
    GatesConfig,
    GitAutomationConfig,
    list_profiles,
    resolve_orchestration_profile,
    update_gates_config,
    update_git_config,
)
from loregarden.services.queue_admission import QueueAdmissionService
from sqlmodel import Session, select

router = APIRouter(prefix="/orchestration", tags=["orchestration"])


def _run_view(run: OrchestrationRun) -> OrchestrationRunView:
    return OrchestrationRunView(
        id=run.id,
        run_code=run.run_code,
        ticket_id=run.ticket_id,
        driver=run.driver,
        profile_slug=run.profile_slug,
        external_harness=run.external_harness,
        status=run.status,
        current_stage_key=run.current_stage_key,
        error_message=run.error_message,
        started_at=run.started_at,
        finished_at=run.finished_at,
    )


def _profile_view(profile, workspace: Workspace) -> OrchestrationProfileView:
    return OrchestrationProfileView(
        slug=profile.slug,
        name=profile.name or profile.slug,
        driver=profile.driver,
        workflow_template=profile.workflow_template,
        orchestrator_skill=profile.orchestrator.skill,
        # Report whether gates *can actually run*, not the raw config flag — a
        # profile that is enabled but has nothing runnable configured gates
        # nothing, and must not read as green (ticket 88).
        gates_enabled=gates_can_run(profile, workspace),
        gates_commands=profile.gates.commands,
        gates_transition_script=profile.gates.transition_script,
        max_stages_per_run=profile.max_stages_per_run,
    )


def _get_run(session: Session, run_id: str) -> OrchestrationRun:
    run = session.get(OrchestrationRun, run_id)
    if not run:
        raise HTTPException(404, "Orchestration run not found")
    return run


@router.get("/workspaces/{slug}/profile", response_model=OrchestrationProfileView)
def get_workspace_profile(
    slug: str, session: Session = Depends(get_session)
) -> OrchestrationProfileView:
    ws = session.exec(select(Workspace).where(Workspace.slug == slug)).first()
    if not ws:
        raise HTTPException(404, "Workspace not found")
    return _profile_view(resolve_orchestration_profile(ws), ws)


@router.get("/workspaces/{slug}/profiles", response_model=list[OrchestrationProfileView])
def list_workspace_profiles(
    slug: str, session: Session = Depends(get_session)
) -> list[OrchestrationProfileView]:
    ws = session.exec(select(Workspace).where(Workspace.slug == slug)).first()
    if not ws:
        raise HTTPException(404, "Workspace not found")
    return [_profile_view(p, ws) for p in list_profiles(ws)]


@router.put("/workspaces/{slug}/profile/gates", response_model=OrchestrationProfileView)
def update_workspace_gates(
    slug: str, body: GatesConfigUpdate, session: Session = Depends(get_session)
) -> OrchestrationProfileView:
    ws = session.exec(select(Workspace).where(Workspace.slug == slug)).first()
    if not ws:
        raise HTTPException(404, "Workspace not found")
    gates = GatesConfig(
        enabled=body.enabled, commands=body.commands, transition_script=body.transition_script
    )
    profile = update_gates_config(ws, gates)
    return _profile_view(profile, ws)


@router.get("/workspaces/{slug}/profile/git", response_model=GitAutomationView)
def get_workspace_git_automation(
    slug: str, session: Session = Depends(get_session)
) -> GitAutomationView:
    ws = session.exec(select(Workspace).where(Workspace.slug == slug)).first()
    if not ws:
        raise HTTPException(404, "Workspace not found")
    profile = resolve_orchestration_profile(ws)
    return GitAutomationView.model_validate(profile.git.model_dump(mode="json"))


@router.put("/workspaces/{slug}/profile/git", response_model=GitAutomationView)
def update_workspace_git_automation(
    slug: str, body: GitAutomationView, session: Session = Depends(get_session)
) -> GitAutomationView:
    ws = session.exec(select(Workspace).where(Workspace.slug == slug)).first()
    if not ws:
        raise HTTPException(404, "Workspace not found")
    profile = update_git_config(
        ws, GitAutomationConfig.model_validate(body.model_dump(mode="json"))
    )
    return GitAutomationView.model_validate(profile.git.model_dump(mode="json"))


@router.post("/tickets/{ticket_id}/start", response_model=OrchestrationRunView)
def start_orchestration(
    ticket_id: str,
    body: StartOrchestrationRequest = Body(default_factory=StartOrchestrationRequest),
    session: Session = Depends(get_session),
) -> OrchestrationRunView:
    svc = OrchestrationCallbackService(session)
    ticket = svc.resolve_ticket(ticket_id=ticket_id)
    ws = session.get(Workspace, ticket.workspace_id)
    if not ws:
        raise HTTPException(404, "Workspace not found")
    profile = resolve_orchestration_profile(ws)
    driver = body.driver or profile.driver

    # Same pool as /orchestrate and MCP: this path used to start work with no
    # slot, so the board showed idle lanes while agents ran.
    reservation = QueueAdmissionService(session).reserve_orchestration(
        ticket,
        auto_approve=body.auto_approve,
        stop_at_stage_key=body.stop_at_stage_key,
        preferred_slot=body.slot_number,
        driver=driver.value if driver else "",
        max_stages=body.max_stages,
        timeout_seconds=body.timeout_seconds,
    )
    if not reservation.admitted:
        raise HTTPException(
            409,
            detail={"status": "queued", **reservation.as_dict()},
        )

    try:
        if driver == OrchestrationDriver.BUILTIN_AUTOPILOT:
            run = BuiltinOrchestrator(session).execute(
                ticket,
                profile,
                max_stages=body.max_stages,
                stop_at_stage_key=body.stop_at_stage_key,
                auto_approve=body.auto_approve,
                timeout_seconds=body.timeout_seconds,
            )
        elif driver == OrchestrationDriver.EXTERNAL_MCP:
            run = svc.start_orchestration_run(
                ticket,
                driver=driver,
                profile_slug=profile.slug,
                auto_approve=body.auto_approve,
                stop_at_stage_key=body.stop_at_stage_key or "",
                timeout_override_seconds=body.timeout_seconds,
            )
        else:
            reservation.release()
            raise ValueError("Use POST /api/tickets/{id}/start for manual_stage driver")
    except ValueError as exc:
        reservation.release()
        raise HTTPException(400, str(exc)) from exc

    reservation.bind(orchestration_run_id=run.id)
    return _run_view(run)


@router.get("/tickets/{ticket_id}/state", response_model=dict)
def get_ticket_orchestration_state(
    ticket_id: str,
    session: Session = Depends(get_session),
) -> dict:
    svc = OrchestrationCallbackService(session)
    ticket = svc.resolve_ticket(ticket_id=ticket_id)
    active = svc.get_active_orchestration_run(ticket.id)
    orch = OrchestrationService(session)
    return {
        "ticket_id": ticket.id,
        "external_id": ticket.external_id,
        # A caller that reached this route under a pre-restructure id gets to see
        # that it is the same ticket, not a different one than it asked for.
        "legacy_external_id": ticket.legacy_external_id,
        "state": ticket.state.value,
        "workflow_stage_key": ticket.workflow_stage_key,
        "workflow_stage_status": ticket.workflow_stage_status.value,
        "next_agent": ticket.next_agent,
        "blocking_issues": ticket.blocking_issues,
        "active_orchestration": _run_view(active).model_dump() if active else None,
        "stages": [s.model_dump() for s in orch.build_stage_views(ticket)],
    }


@router.get("/tickets/by-external/{workspace_slug}/{external_id}/state", response_model=dict)
def get_ticket_state_by_external(
    workspace_slug: str,
    external_id: str,
    session: Session = Depends(get_session),
) -> dict:
    svc = OrchestrationCallbackService(session)
    ticket = svc.resolve_ticket(external_id=external_id, workspace_slug=workspace_slug)
    return get_ticket_orchestration_state(ticket.id, session)


@router.post("/runs/{run_id}/start_stage", response_model=dict)
def callback_start_stage(
    run_id: str,
    body: StartStageRequest,
    session: Session = Depends(get_session),
) -> dict:
    svc = OrchestrationCallbackService(session)
    run = _get_run(session, run_id)
    ticket = svc.resolve_ticket(ticket_id=run.ticket_id)
    try:
        svc.start_stage(run, ticket, stage_key=body.stage_key, agent_id=body.agent_id)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    session.refresh(ticket)
    return {"ok": True, "stage_key": body.stage_key, "ticket_state": ticket.state.value}


@router.post("/runs/{run_id}/complete_stage", response_model=dict)
def callback_complete_stage(
    run_id: str,
    body: CompleteStageRequest,
    session: Session = Depends(get_session),
) -> dict:
    svc = OrchestrationCallbackService(session)
    run = _get_run(session, run_id)
    ticket = svc.resolve_ticket(ticket_id=run.ticket_id)
    try:
        svc.complete_stage(
            run,
            ticket,
            stage_key=body.stage_key,
            next_agent=body.next_agent,
            next_stage_key=body.next_stage_key,
            outcome=body.outcome,
            blocking_issues=body.blocking_issues,
            advance=body.advance,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    session.refresh(ticket)
    return {
        "ok": True,
        "stage_key": body.stage_key,
        "workflow_stage_key": ticket.workflow_stage_key,
        "ticket_state": ticket.state.value,
    }


@router.post("/runs/{run_id}/skip_stage", response_model=dict)
def callback_skip_stage(
    run_id: str,
    body: SkipStageRequest,
    session: Session = Depends(get_session),
) -> dict:
    svc = OrchestrationCallbackService(session)
    run = _get_run(session, run_id)
    ticket = svc.resolve_ticket(ticket_id=run.ticket_id)
    try:
        svc.skip_stage(run, ticket, stage_key=body.stage_key, reason=body.reason)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"ok": True, "stage_key": body.stage_key}


@router.post("/runs/{run_id}/block", response_model=dict)
def callback_block(
    run_id: str,
    body: BlockTicketRequest,
    session: Session = Depends(get_session),
) -> dict:
    svc = OrchestrationCallbackService(session)
    run = _get_run(session, run_id)
    ticket = svc.resolve_ticket(ticket_id=run.ticket_id)
    svc.block_ticket(run, ticket, stage_key=body.stage_key, message=body.message)
    return {"ok": True, "ticket_state": ticket.state.value}


@router.post("/runs/{run_id}/attach_artifact", response_model=dict)
def callback_attach_artifact(
    run_id: str,
    body: AttachArtifactRequest,
    session: Session = Depends(get_session),
) -> dict:
    svc = OrchestrationCallbackService(session)
    run = _get_run(session, run_id)
    ticket = svc.resolve_ticket(ticket_id=run.ticket_id)
    artifact = svc.attach_artifact(
        ticket,
        kind=body.kind,
        title=body.title,
        content=body.content,
    )
    return {"ok": True, "artifact_id": artifact.id}


@router.post("/runs/{run_id}/request_approval", response_model=dict)
def callback_request_approval(
    run_id: str,
    body: RequestApprovalRequest,
    session: Session = Depends(get_session),
) -> dict:
    svc = OrchestrationCallbackService(session)
    run = _get_run(session, run_id)
    ticket = svc.resolve_ticket(ticket_id=run.ticket_id)
    approval = svc.request_approval(
        ticket,
        stage_key=body.stage_key,
        title=body.title,
        impact=body.impact,
        level=body.level,
    )
    return {"ok": True, "approval_id": approval.id}


@router.post("/runs/{run_id}/complete", response_model=OrchestrationRunView)
def callback_complete_orchestration(
    run_id: str,
    body: CompleteOrchestrationRequest,
    session: Session = Depends(get_session),
) -> OrchestrationRunView:
    svc = OrchestrationCallbackService(session)
    run = _get_run(session, run_id)
    ticket = svc.resolve_ticket(ticket_id=run.ticket_id)
    run = svc.complete_orchestration(
        run,
        ticket,
        status=body.status,
        message=body.message,
    )
    return _run_view(run)
