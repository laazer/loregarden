from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session, select

from loregarden.core.workflow_loader import get_template_stages
from loregarden.db.session import get_session
from loregarden.models.domain import (
    Ticket,
    TicketState,
    Workspace,
    WorkspaceCreate,
    WorkspaceTemplateUpdate,
    WorkflowTemplate,
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
            }
        )
    return result


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
