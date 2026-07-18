from fastapi import APIRouter, Depends, HTTPException
from loregarden.db.session import get_session
from loregarden.models.domain import (
    StudioAgentCreate,
    StudioAgentPreviewRequest,
    StudioAgentUpdate,
    StudioGenerateRequest,
    StudioWorkflowCreate,
    StudioWorkflowUpdate,
)
from loregarden.services.studio_service import StudioService
from sqlmodel import Session

router = APIRouter(prefix="/studio", tags=["studio"])


@router.get("/mcp-tools")
def list_mcp_tools(session: Session = Depends(get_session)) -> list[str]:
    return StudioService(session).list_mcp_tools()


@router.get("/mcp-tool-guides")
def list_mcp_tool_guides(session: Session = Depends(get_session)) -> list[dict]:
    return [item.model_dump() for item in StudioService(session).list_mcp_tool_guides()]


@router.get("/defaults")
def studio_defaults(session: Session = Depends(get_session)) -> dict:
    return StudioService(session).agent_defaults()


@router.post("/agents/preview")
def preview_studio_agent(
    body: StudioAgentPreviewRequest, session: Session = Depends(get_session)
) -> dict:
    return StudioService(session).preview_agent(body).model_dump()


@router.post("/agents/generate")
def generate_studio_agent(
    body: StudioGenerateRequest, session: Session = Depends(get_session)
) -> dict:
    try:
        return StudioService(session).generate_agent(body.description).model_dump()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except TimeoutError as exc:
        raise HTTPException(status_code=504, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.get("/agents")
def list_studio_agents(session: Session = Depends(get_session)) -> list[dict]:
    return [item.model_dump() for item in StudioService(session).list_agents()]


@router.get("/agents/{slug}")
def get_studio_agent(slug: str, session: Session = Depends(get_session)) -> dict:
    agent = StudioService(session).get_agent(slug)
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    return agent.model_dump()


@router.post("/agents")
def create_studio_agent(body: StudioAgentCreate, session: Session = Depends(get_session)) -> dict:
    try:
        return StudioService(session).create_agent(body).model_dump()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.patch("/agents/{slug}")
def update_studio_agent(
    slug: str,
    body: StudioAgentUpdate,
    session: Session = Depends(get_session),
) -> dict:
    try:
        return StudioService(session).update_agent(slug, body).model_dump()
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.delete("/agents/{slug}")
def delete_studio_agent(slug: str, session: Session = Depends(get_session)) -> dict:
    try:
        StudioService(session).delete_agent(slug)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"ok": True}


@router.get("/agents/{slug}/versions")
def list_studio_agent_versions(slug: str, session: Session = Depends(get_session)) -> list[dict]:
    try:
        return [v.model_dump() for v in StudioService(session).list_agent_versions(slug)]
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/agents/{slug}/versions/{version}")
def get_studio_agent_version(
    slug: str, version: int, session: Session = Depends(get_session)
) -> dict:
    try:
        return StudioService(session).get_agent_version(slug, version).model_dump()
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/agents/{slug}/versions/{version}/restore")
def restore_studio_agent_version(
    slug: str, version: int, session: Session = Depends(get_session)
) -> dict:
    try:
        return StudioService(session).restore_agent_version(slug, version).model_dump()
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/workflows")
def list_studio_workflows(session: Session = Depends(get_session)) -> list[dict]:
    return [item.model_dump() for item in StudioService(session).list_workflows()]


@router.post("/workflows/generate")
def generate_studio_workflow(
    body: StudioGenerateRequest, session: Session = Depends(get_session)
) -> dict:
    try:
        return StudioService(session).generate_workflow(body.description).model_dump()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except TimeoutError as exc:
        raise HTTPException(status_code=504, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.get("/workflows/{slug}")
def get_studio_workflow(slug: str, session: Session = Depends(get_session)) -> dict:
    workflow = StudioService(session).get_workflow(slug)
    if not workflow:
        raise HTTPException(status_code=404, detail="Workflow not found")
    return workflow.model_dump()


@router.post("/workflows")
def create_studio_workflow(
    body: StudioWorkflowCreate, session: Session = Depends(get_session)
) -> dict:
    try:
        return StudioService(session).create_workflow(body).model_dump()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.patch("/workflows/{slug}")
def update_studio_workflow(
    slug: str,
    body: StudioWorkflowUpdate,
    session: Session = Depends(get_session),
) -> dict:
    try:
        return StudioService(session).update_workflow(slug, body).model_dump()
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.delete("/workflows/{slug}")
def delete_studio_workflow(slug: str, session: Session = Depends(get_session)) -> dict:
    try:
        StudioService(session).delete_workflow(slug)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"ok": True}


@router.post("/workflows/{slug}/publish")
def publish_studio_workflow(slug: str, session: Session = Depends(get_session)) -> dict:
    try:
        return StudioService(session).publish_workflow(slug).model_dump()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/workflows/{slug}/versions")
def list_studio_workflow_versions(slug: str, session: Session = Depends(get_session)) -> list[dict]:
    try:
        return [v.model_dump() for v in StudioService(session).list_workflow_versions(slug)]
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/workflows/{slug}/versions/{version}")
def get_studio_workflow_version(
    slug: str, version: int, session: Session = Depends(get_session)
) -> dict:
    try:
        return StudioService(session).get_workflow_version(slug, version).model_dump()
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/workflows/{slug}/versions/{version}/restore")
def restore_studio_workflow_version(
    slug: str, version: int, session: Session = Depends(get_session)
) -> dict:
    try:
        return StudioService(session).restore_workflow_version(slug, version).model_dump()
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
