from fastapi import APIRouter, Depends, HTTPException
from loregarden.db.session import get_session
from loregarden.models.domain import (
    Approval,
    ApprovalAction,
    ApprovalKind,
    ApprovalStatus,
    Ticket,
    TicketState,
    Workspace,
)
from loregarden.services.approval_views import approval_to_view
from loregarden.services.orchestration import ApprovalService, OrchestrationService
from loregarden.services.orchestration_callbacks import OrchestrationCallbackService
from loregarden.services.prepared_action import (
    PREPARED_ACTION_EVIDENCE_KIND,
    PreparedAction,
    run_prepared_action,
)
from loregarden.services.ticket_state_service import choose
from pydantic import ValidationError
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
            route_to_stage_key=body.route_to_stage_key,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"id": approval.id, "status": approval.status.value}


def _runnable_human_action(
    session: Session, approval_id: str
) -> tuple[Approval, PreparedAction, Ticket, Workspace]:
    """Everything the runner needs, or the reason it cannot run.

    Split from the endpoint so each refusal stays a plain named check — this is
    the boundary where an agent-supplied payload becomes something the control
    plane executes, and it should read as a list of what must be true.
    """
    approval = session.get(Approval, approval_id)
    if not approval:
        raise HTTPException(404, "Approval not found")
    if approval.kind is not ApprovalKind.HUMAN_ACTION:
        raise HTTPException(400, "Not a human action")
    if approval.status is not ApprovalStatus.PENDING:
        raise HTTPException(400, f"Already {approval.status.value}")

    try:
        action = PreparedAction.model_validate_json(approval.tool_input_json or "{}")
    except ValidationError as exc:
        raise HTTPException(400, f"Approval carries no usable prepared action: {exc}") from exc
    if not action.is_runnable():
        raise HTTPException(
            400,
            f"Tier '{action.tier.value}' is not runnable here — only 'one_click' actions "
            "carry a committed script the control plane can run.",
        )

    ticket = session.get(Ticket, approval.ticket_id) if approval.ticket_id else None
    if not ticket:
        raise HTTPException(400, "Approval is not attached to a ticket")
    workspace = session.get(Workspace, ticket.workspace_id)
    if not workspace:
        raise HTTPException(400, "Ticket has no workspace")
    return approval, action, ticket, workspace


@router.post("/approvals/{approval_id}/run")
def run_human_action(
    approval_id: str,
    session: Session = Depends(get_session),
) -> dict:
    """Run a one-click prepared action, capture what it said, and clear the block.

    This is the half of lg-workflow-integrity-460 that stops a result being
    *transcribed*: the person says go, the script runs, its output lands on the
    ticket as evidence, and the block lifts without a separate requeue. A person
    reading numbers off a screen and retyping them was most of the work the
    original handover created.

    Only ONE_CLICK actions run, and only via their committed `script_path` —
    never the `command` string, which an agent wrote.
    """
    approval, action, ticket, workspace = _runnable_human_action(session, approval_id)

    result = run_prepared_action(repo_path=workspace.repo_path, action=action)
    callbacks = OrchestrationCallbackService(session)
    callbacks.attach_artifact(
        ticket,
        kind="context" if result.ok else "error",
        title=f"Human action — {action.command or action.script_path}",
        content={
            "message": result.output or result.error,
            "run_code": "",
            "agent_id": "",
            "stage_key": approval.stage_key,
            "command": action.command or action.script_path,
        },
        evidence_kind=PREPARED_ACTION_EVIDENCE_KIND if result.ok else "",
    )
    if not result.ok:
        # The block stays. A failed capture has not answered the question, and
        # clearing it here would report success the run never had.
        return {
            "ok": False,
            "exit_code": result.exit_code,
            "error": result.error,
            "ticket_state": ticket.state.value,
        }

    approval.status = ApprovalStatus.APPROVED
    session.add(approval)
    ticket.blocking_issues = ""
    if ticket.state is TicketState.BLOCKED:
        choose(session, ticket, TicketState.IN_PROGRESS, actor="human", emit=False)
    if approval.stage_key:
        OrchestrationService(session).refresh_stage_retry_budget(ticket, approval.stage_key)
    session.add(ticket)
    session.commit()
    return {
        "ok": True,
        "exit_code": result.exit_code,
        "output": result.output,
        "ticket_state": ticket.state.value,
    }
