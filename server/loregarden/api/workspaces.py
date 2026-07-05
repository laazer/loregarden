from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session, select

from loregarden.core.workflow_loader import get_template_stages
from loregarden.db.session import get_session
from loregarden.models.domain import (
    Ticket,
    TicketState,
    Workspace,
    WorkspaceCreate,
    WorkspaceRuntimeSettings,
    WorkspaceRuntimeUpdate,
    WorkspaceTemplateUpdate,
    WorkflowTemplate,
)
from loregarden.services.cli_settings import (
    VALID_CLI_ADAPTERS,
    runtime_options_payload,
    workspace_cli_settings,
)
from loregarden.services.workspace_paths import (
    resolve_workspace_root,
    workspace_repo_exists,
)
from loregarden.services.workflow_service import WorkflowService, resolve_workspace_stages

router = APIRouter(prefix="/workspaces", tags=["workspaces"])


def _template_slug(session: Session, workspace: Workspace) -> str:
    if not workspace.workflow_template_id:
        return ""
    tpl = session.get(WorkflowTemplate, workspace.workflow_template_id)
    return tpl.slug if tpl else ""


@router.get("")
def list_workspaces(session: Session = Depends(get_session)) -> list[dict]:
    workspaces = session.exec(select(Workspace)).all()
    result = []
    for ws in workspaces:
        tickets = session.exec(select(Ticket).where(Ticket.workspace_id == ws.id)).all()
        blocked = sum(1 for t in tickets if t.state == TicketState.BLOCKED)
        result.append(
            {
                "id": ws.id,
                "slug": ws.slug,
                "name": ws.name,
                "repo_path": ws.repo_path,
                "repo_root": str(resolve_workspace_root(ws)),
                "repo_exists": workspace_repo_exists(ws),
                "ticket_count": len(tickets),
                "blocked_count": blocked,
                "workflow_template_slug": _template_slug(session, ws),
                **workspace_cli_settings(ws).__dict__,
            }
        )
    return result


@router.get("/runtime-options")
def get_runtime_options() -> dict:
    return runtime_options_payload()


@router.get("/{slug}/runtime", response_model=WorkspaceRuntimeSettings)
def get_workspace_runtime(slug: str, session: Session = Depends(get_session)) -> WorkspaceRuntimeSettings:
    ws = session.exec(select(Workspace).where(Workspace.slug == slug)).first()
    if not ws:
        raise HTTPException(404, "Workspace not found")
    return WorkspaceRuntimeSettings(**workspace_cli_settings(ws).__dict__)


@router.patch("/{slug}/runtime", response_model=WorkspaceRuntimeSettings)
def update_workspace_runtime(
    slug: str,
    body: WorkspaceRuntimeUpdate,
    session: Session = Depends(get_session),
) -> WorkspaceRuntimeSettings:
    ws = session.exec(select(Workspace).where(Workspace.slug == slug)).first()
    if not ws:
        raise HTTPException(404, "Workspace not found")
    if body.cli_adapter not in VALID_CLI_ADAPTERS:
        raise HTTPException(400, f"Invalid cli_adapter: {body.cli_adapter}")
    ws.cli_adapter = body.cli_adapter
    ws.claude_model = body.claude_model.strip()
    ws.cursor_model = body.cursor_model.strip()
    ws.lmstudio_base_url = body.lmstudio_base_url.strip()
    ws.lmstudio_model = body.lmstudio_model.strip()
    session.add(ws)
    session.commit()
    session.refresh(ws)
    return WorkspaceRuntimeSettings(**workspace_cli_settings(ws).__dict__)


@router.post("", status_code=201)
def create_workspace(
    body: WorkspaceCreate,
    session: Session = Depends(get_session),
) -> dict:
    svc = WorkflowService(session)
    try:
        ws = svc.create_workspace(
            slug=body.slug,
            name=body.name,
            workflow_template_slug=body.workflow_template_slug,
            repo_path=body.repo_path,
            orchestration_profile_slug=body.orchestration_profile_slug,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {
        "id": ws.id,
        "slug": ws.slug,
        "name": ws.name,
        "workflow_template_slug": body.workflow_template_slug,
    }


@router.patch("/{slug}/workflow")
def update_workspace_workflow(
    slug: str,
    body: WorkspaceTemplateUpdate,
    session: Session = Depends(get_session),
) -> dict:
    svc = WorkflowService(session)
    try:
        ws = svc.set_workspace_template(slug, body.workflow_template_slug)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {
        "slug": ws.slug,
        "workflow_template_slug": body.workflow_template_slug,
    }


@router.get("/{slug}/workflow")
def get_workspace_workflow(slug: str, session: Session = Depends(get_session)) -> dict:
    ws = session.exec(select(Workspace).where(Workspace.slug == slug)).first()
    if not ws:
        raise HTTPException(404, "Workspace not found")
    template, stages = resolve_workspace_stages(session, ws)
    if not template:
        return {"stages": [], "template_slug": "", "template_name": ""}
    return {
        "template_slug": template.slug,
        "template_name": template.name,
        "stages": [s.model_dump() for s in stages],
    }
