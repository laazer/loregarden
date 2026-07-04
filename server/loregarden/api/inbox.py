import json

from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session

from loregarden.db.session import get_session
from loregarden.models.domain import ApprovalAction
from loregarden.services.orchestration import ApprovalService

router = APIRouter(prefix="/inbox", tags=["inbox"])


@router.get("/approvals")
def list_approvals(session: Session = Depends(get_session)) -> list[dict]:
    from sqlmodel import select

    from loregarden.core.workflow_loader import stage_display_name
    from loregarden.models.domain import Approval, ApprovalStatus, Ticket, Workspace, WorkflowTemplate

    approvals = session.exec(
        select(Approval).where(Approval.status == ApprovalStatus.PENDING)
    ).all()
    views = []
    for a in approvals:
        ticket = session.get(Ticket, a.ticket_id)
        ws = session.get(Workspace, a.workspace_id)
        stage_name = a.stage_key
        if ws and ws.workflow_template_id:
            tpl = session.get(WorkflowTemplate, ws.workflow_template_id)
            if tpl:
                stage_name = stage_display_name(tpl, a.stage_key)
        views.append(
            {
                "id": a.id,
                "title": a.title,
                "level": a.level,
                "workspace_slug": ws.slug if ws else "",
                "stage_key": a.stage_key,
                "stage_name": stage_name,
                "impact": a.impact,
                "ticket_id": a.ticket_id,
                "ticket_external_id": ticket.external_id if ticket else "",
            }
        )
    return views


@router.post("/approvals/{approval_id}")
def resolve_approval(
    approval_id: str,
    body: ApprovalAction,
    session: Session = Depends(get_session),
) -> dict:
    svc = ApprovalService(session)
    approved = body.action == "approve"
    try:
        approval = svc.resolve(approval_id, approved=approved)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"id": approval.id, "status": approval.status.value}
