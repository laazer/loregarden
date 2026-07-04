from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session, select

from loregarden.db.session import get_session
from loregarden.models.domain import WorkspaceCreate, WorkspaceTemplateUpdate
from loregarden.services.workflow_service import WorkflowService, resolve_workspace_stages

router = APIRouter(prefix="/workflows", tags=["workflows"])


@router.get("/templates")
def list_workflow_templates(session: Session = Depends(get_session)) -> list[dict]:
    svc = WorkflowService(session)
    return [
        {
            "id": t.id,
            "slug": t.slug,
            "name": t.name,
            "description": t.description,
            "source_path": t.source_path,
            "stage_count": len(__import__("json").loads(t.stages_json or "[]")),
        }
        for t in svc.list_templates()
    ]
