"""REST API for orchestration callbacks — used by MCP server and external drivers."""

from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException
from loregarden.api.operation_bodies import OperationBody
from loregarden.api.operation_routes import run_rest_operation
from loregarden.core.terminal_text import strip_ansi
from loregarden.db.session import get_session
from loregarden.models.domain import (
    FindingDecision,
    GateOutcome,
    GatesConfigUpdate,
    GateTestCommandResult,
    GateTestReport,
    GateTestRequest,
    GitAutomationView,
    OrchestrationProfileView,
    OrchestrationRunView,
    Workspace,
)
from loregarden.services.discovered_work import undecided
from loregarden.services.gate_runner import (
    dry_run_gate_commands,
    gates_can_run,
    resolved_transition_script,
    sample_gate_context,
    transition_name,
)
from loregarden.services.operations import OperationId
from loregarden.services.orchestration_callbacks import OrchestrationCallbackService
from loregarden.services.orchestration_profile import (
    PRESERVED_GATE_KEYS,
    GatesConfig,
    GitAutomationConfig,
    ProfileUnreadableError,
    default_gate_commands,
    list_profiles,
    resolve_orchestration_profile,
    update_gates_config,
    update_git_config,
)
from loregarden.services.scope_round import decide_finding
from pydantic import BaseModel
from sqlmodel import Session, select

router = APIRouter(prefix="/orchestration", tags=["orchestration"])


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
        gates_configured=profile.gates.enabled,
        gates_commands=profile.gates.commands,
        gates_transition_script=profile.gates.transition_script,
        gates_transition_script_resolved=resolved_transition_script(profile, workspace),
        gates_autofix_commands=profile.gates.autofix_commands,
        gates_autofix_agent_fallback=profile.gates.autofix_agent_fallback,
        gates_autofix_max_agent_attempts=profile.gates.autofix_max_agent_attempts,
        gates_placeholders=sample_gate_context(workspace),
        gates_suggested_commands=default_gate_commands(),
        max_stages_per_run=profile.max_stages_per_run,
        source_path=profile.source_path,
    )


@router.get("/workspaces/{slug}/profile", response_model=OrchestrationProfileView)
def get_workspace_profile(
    slug: str, session: Session = Depends(get_session)
) -> OrchestrationProfileView:
    ws = session.exec(select(Workspace).where(Workspace.slug == slug)).first()
    if not ws:
        raise HTTPException(404, "Workspace not found")
    # The Gates editor is the surface that *writes* these files, so a
    # half-written one is its own output coming back. Unguarded this was a 500,
    # and the panel rendered a plausible, complete-looking gate list over a file
    # nobody could read (lg-milestone-that-1215 S8). The view is inside the
    # guard as well as the resolve: `_profile_view` reads `default.yaml` through
    # `default_gate_commands()`, so this endpoint can fail on a file that is not
    # the workspace's own. Only `ProfileUnreadableError` — anything broader
    # would start telling the operator to fix a profile that is fine.
    try:
        return _profile_view(resolve_orchestration_profile(ws), ws)
    except ProfileUnreadableError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/workspaces/{slug}/profiles", response_model=list[OrchestrationProfileView])
def list_workspace_profiles(
    slug: str, session: Session = Depends(get_session)
) -> list[OrchestrationProfileView]:
    ws = session.exec(select(Workspace).where(Workspace.slug == slug)).first()
    if not ws:
        raise HTTPException(404, "Workspace not found")
    # `list_profiles` globs the whole directory and propagates rather than
    # skipping the file it cannot read (R4): a silently shortened list is a
    # complete-looking editor missing the one file the operator is about to
    # overwrite.
    try:
        return [_profile_view(p, ws) for p in list_profiles(ws)]
    except ProfileUnreadableError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.put("/workspaces/{slug}/profile/gates", response_model=OrchestrationProfileView)
def update_workspace_gates(
    slug: str, body: GatesConfigUpdate, session: Session = Depends(get_session)
) -> OrchestrationProfileView:
    ws = session.exec(select(Workspace).where(Workspace.slug == slug)).first()
    if not ws:
        raise HTTPException(404, "Workspace not found")
    supplied = body.model_dump(exclude_none=True)
    try:
        profile = update_gates_config(
            ws,
            GatesConfig.model_validate(supplied),
            preserve=PRESERVED_GATE_KEYS - supplied.keys(),
        )
    except PermissionError as exc:
        # The editor shows this as a permission problem with the named file,
        # not a server error (lg-gate-studio-863, 631's R3).
        raise HTTPException(
            403, f"Cannot write the gate settings for {slug}: {exc.filename} is not writable"
        ) from exc
    return _profile_view(profile, ws)


@router.post("/workspaces/{slug}/profile/gates/test", response_model=GateTestReport)
def test_workspace_gates(
    slug: str, body: GateTestRequest, session: Session = Depends(get_session)
) -> GateTestReport:
    """Run draft gate commands against the workspace's shared checkout, with
    sample ticket values, and report every command's verdict. Nothing is saved."""
    ws = session.exec(select(Workspace).where(Workspace.slug == slug)).first()
    if not ws:
        raise HTTPException(404, "Workspace not found")
    runs = dry_run_gate_commands(
        ws, body.commands, from_stage=body.from_stage, to_stage=body.to_stage
    )
    return GateTestReport(
        repo_root=sample_gate_context(ws)["workspace_root"],
        transition=transition_name(body.from_stage, body.to_stage),
        results=[
            GateTestCommandResult(
                template=run.template,
                command=run.command,
                outcome=run.result.outcome or GateOutcome.FAILED,
                message=run.result.message,
                stdout=strip_ansi(run.result.stdout),
                stderr=strip_ansi(run.result.stderr),
                duration_ms=run.duration_ms,
            )
            for run in runs
        ],
    )


@router.get("/workspaces/{slug}/profile/git", response_model=GitAutomationView)
def get_workspace_git_automation(
    slug: str, session: Session = Depends(get_session)
) -> GitAutomationView:
    ws = session.exec(select(Workspace).where(Workspace.slug == slug)).first()
    if not ws:
        raise HTTPException(404, "Workspace not found")
    try:
        profile = resolve_orchestration_profile(ws)
    except ProfileUnreadableError as exc:
        raise HTTPException(400, str(exc)) from exc
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


class StartStageRequest(OperationBody, operation=OperationId.STAGE_START, supplied={"run_id"}):
    """A stage to start on the run the path names."""


class CompleteStageRequest(
    OperationBody, operation=OperationId.STAGE_COMPLETE, supplied={"run_id"}
):
    """A stage's result, on the run the path names."""


class SkipStageRequest(OperationBody, operation=OperationId.STAGE_SKIP, supplied={"run_id"}):
    """A stage to skip on the run the path names."""


class BlockTicketRequest(OperationBody, operation=OperationId.TICKET_BLOCK, supplied={"run_id"}):
    """Why the run the path names cannot go on."""


class AttachArtifactRequest(
    OperationBody,
    operation=OperationId.ARTIFACT_ATTACH,
    supplied={"run_id"},
    # An object here; `content_json` is MCP's way to send it as text.
    hidden={"content_json"},
):
    """An artifact for the run the path names."""


class RequestApprovalRequest(
    OperationBody, operation=OperationId.APPROVAL_REQUEST, supplied={"run_id"}
):
    """A question for a person, from the run the path names."""


class CompleteOrchestrationRequest(
    OperationBody, operation=OperationId.ORCHESTRATION_COMPLETE, supplied={"run_id"}
):
    """How the run the path names ended."""


class OrchestrationStartRequest(
    OperationBody, operation=OperationId.ORCHESTRATION_START, supplied={"ticket_id"}
):
    """An orchestration of the ticket the path names.

    `/api/tickets/{id}/orchestrate` keeps its own model: it admits past
    prerequisites, which an orchestration start never checks.
    """


@router.post("/tickets/{ticket_id}/start", response_model=OrchestrationRunView)
def start_orchestration(
    ticket_id: str,
    body: OrchestrationStartRequest = Body(default_factory=OrchestrationStartRequest),
    session: Session = Depends(get_session),
) -> OrchestrationRunView:
    ticket = OrchestrationCallbackService(session).resolve_ticket(ticket_id=ticket_id)
    if undecided(session, ticket.id) is not None:
        # A run's finding is held out of the work until its scope is decided.
        # A person pressing Run on it has decided: it is work, its parent's.
        # Only a person decides that, so it stays here, ahead of the operation.
        decide_finding(
            session,
            ticket,
            FindingDecision.FOLLOW_UP,
            reason="An operator started it.",
            decided_by="operator",
        )
    return run_rest_operation(
        session,
        OperationId.ORCHESTRATION_START,
        {**body.model_dump(), "ticket_id": ticket.id},
    )


@router.get("/tickets/{ticket_id}/state", response_model=dict)
def get_ticket_orchestration_state(
    ticket_id: str,
    session: Session = Depends(get_session),
) -> dict:
    return run_rest_operation(session, OperationId.TICKET_STATE, {"ticket_id": ticket_id})


@router.get("/tickets/by-external/{workspace_slug}/{external_id}/state", response_model=dict)
def get_ticket_state_by_external(
    workspace_slug: str,
    external_id: str,
    session: Session = Depends(get_session),
) -> dict:
    return run_rest_operation(
        session,
        OperationId.TICKET_STATE,
        {"external_id": external_id, "workspace_slug": workspace_slug},
    )


def _naming_run(run_id: str, body: BaseModel) -> dict[str, Any]:
    """A callback's arguments: its body, and the run its path names."""
    return {**body.model_dump(), "run_id": run_id}


@router.post("/runs/{run_id}/start_stage", response_model=dict)
def callback_start_stage(
    run_id: str, body: StartStageRequest, session: Session = Depends(get_session)
) -> dict:
    return run_rest_operation(session, OperationId.STAGE_START, _naming_run(run_id, body))


@router.post("/runs/{run_id}/complete_stage", response_model=dict)
def callback_complete_stage(
    run_id: str, body: CompleteStageRequest, session: Session = Depends(get_session)
) -> dict:
    return run_rest_operation(session, OperationId.STAGE_COMPLETE, _naming_run(run_id, body))


@router.post("/runs/{run_id}/skip_stage", response_model=dict)
def callback_skip_stage(
    run_id: str, body: SkipStageRequest, session: Session = Depends(get_session)
) -> dict:
    return run_rest_operation(session, OperationId.STAGE_SKIP, _naming_run(run_id, body))


@router.post("/runs/{run_id}/block", response_model=dict)
def callback_block(
    run_id: str, body: BlockTicketRequest, session: Session = Depends(get_session)
) -> dict:
    return run_rest_operation(session, OperationId.TICKET_BLOCK, _naming_run(run_id, body))


@router.post("/runs/{run_id}/attach_artifact", response_model=dict)
def callback_attach_artifact(
    run_id: str, body: AttachArtifactRequest, session: Session = Depends(get_session)
) -> dict:
    # The writer-owned-kind refusal lives in the `artifact_attach` operation
    # itself (lg-milestone-that-1423): this router has no auth dependency and
    # `body.kind` reaches the row verbatim, and the operation is the one door
    # every transport now shares. Its `ValueError` is a 400 here, as
    # `run_rest_operation` maps every service refusal.
    return run_rest_operation(session, OperationId.ARTIFACT_ATTACH, _naming_run(run_id, body))


@router.post("/runs/{run_id}/request_approval", response_model=dict)
def callback_request_approval(
    run_id: str, body: RequestApprovalRequest, session: Session = Depends(get_session)
) -> dict:
    return run_rest_operation(session, OperationId.APPROVAL_REQUEST, _naming_run(run_id, body))


@router.post("/runs/{run_id}/complete", response_model=OrchestrationRunView)
def callback_complete_orchestration(
    run_id: str, body: CompleteOrchestrationRequest, session: Session = Depends(get_session)
) -> OrchestrationRunView:
    return run_rest_operation(
        session, OperationId.ORCHESTRATION_COMPLETE, _naming_run(run_id, body)
    )
