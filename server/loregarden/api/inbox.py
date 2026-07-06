from fastapi import APIRouter, Depends, HTTPException
from loregarden.db.session import get_session
from loregarden.models.domain import ApprovalAction
from loregarden.services.approval_views import approval_to_view
from loregarden.services.orchestration import ApprovalService
from sqlmodel import Session

router = APIRouter(prefix="/inbox", tags=["inbox"])


@router.get("/approvals")
def list_approvals(
    ticket_id: str | None = None,
    session: Session = Depends(get_session),
) -> list[dict]:
    from loregarden.models.domain import Approval, ApprovalStatus
    from loregarden.services.hierarchy_service import collect_ticket_scope_ids
    from sqlmodel import col, select

    query = select(Approval).where(Approval.status == ApprovalStatus.PENDING)
    if ticket_id:
        scope_ids = collect_ticket_scope_ids(session, ticket_id)
        query = query.where(col(Approval.ticket_id).in_(scope_ids))
    query = query.order_by(Approval.created_at.asc())
    approvals = session.exec(query).all()
    return [approval_to_view(session, item) for item in approvals]


@router.post("/approvals/{approval_id}")
def resolve_approval(
    approval_id: str,
    body: ApprovalAction,
    session: Session = Depends(get_session),
) -> dict:
    svc = ApprovalService(session)
    approved = body.action == "approve"
    try:
        approval = svc.resolve(
            approval_id,
            approved=approved,
            answers=body.answers,
            response_text=body.response,
            always_allow=body.always_allow,
            allow_for_ticket=body.allow_for_ticket,
            allow_for_stage=body.allow_for_stage,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"id": approval.id, "status": approval.status.value}
