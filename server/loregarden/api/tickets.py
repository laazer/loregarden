import json

from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session, col, select

from loregarden.db.session import get_session
from loregarden.models.domain import (
    AdvanceStageRequest,
    Artifact,
    Cycle,
    StartRunRequest,
    Ticket,
    TicketDetail,
    TicketState,
    TicketSummary,
    TicketTreeNode,
    UpdateTicketRequest,
    WorkItemType,
    Workspace,
)
from loregarden.services.hierarchy_service import build_tree, child_count
from loregarden.services.orchestration import OrchestrationService
from loregarden.services.run_service import RunService

router = APIRouter(prefix="/tickets", tags=["tickets"])


def _latest_run_code(session: Session, ticket_id: str) -> str:
    from loregarden.models.domain import AgentRun

    run = session.exec(
        select(AgentRun)
        .where(AgentRun.ticket_id == ticket_id)
        .order_by(AgentRun.created_at.desc())
    ).first()
    return run.run_code if run else ""


def _cycle_name(session: Session, cycle_id: str | None) -> str:
    if not cycle_id:
        return ""
    cycle = session.get(Cycle, cycle_id)
    return cycle.name if cycle else ""


def _ticket_summary(session: Session, ticket: Ticket) -> TicketSummary:
    ws = session.get(Workspace, ticket.workspace_id)
    orch = OrchestrationService(session)
    template = orch.get_template_for_ticket(ticket)
    stage_name = ""
    if template and ticket.workflow_stage_key:
        from loregarden.core.workflow_loader import stage_display_name

        stage_name = stage_display_name(template, ticket.workflow_stage_key)
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
        branch=ticket.branch,
        run_code=_latest_run_code(session, ticket.id),
        work_item_type=ticket.work_item_type,
        parent_ticket_id=ticket.parent_ticket_id,
        cycle_id=ticket.cycle_id,
        cycle_name=_cycle_name(session, ticket.cycle_id),
        milestone=ticket.milestone,
        child_count=child_count(session, ticket.id),
    )


def _artifacts_grouped(session: Session, ticket_id: str) -> dict:
    artifacts = session.exec(
        select(Artifact).where(Artifact.ticket_id == ticket_id)
    ).all()
    grouped: dict = {"diff": None, "logs": [], "tests": None, "context": []}
    for art in artifacts:
        content = json.loads(art.content_json or "{}")
        if art.kind == "diff":
            grouped["diff"] = content
        elif art.kind == "log":
            grouped["logs"] = content.get("lines", [])
            grouped["live"] = content.get("live")
        elif art.kind == "test":
            grouped["tests"] = content
        elif art.kind == "context":
            grouped["context"].append(content)
    return grouped


def _workspace_filter(session: Session, workspace: str | None):
    if not workspace:
        return None
    ws = session.exec(select(Workspace).where(Workspace.slug == workspace)).first()
    if not ws:
        return False
    return ws


@router.get("/tree", response_model=list[TicketTreeNode])
def ticket_tree(
    *,
    workspace: str | None = None,
    state: TicketState | None = None,
    work_item_type: WorkItemType | None = None,
    cycle_id: str | None = None,
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
    if state:
        query = query.where(Ticket.state == state)
    if work_item_type:
        query = query.where(Ticket.work_item_type == work_item_type)
    if cycle_id:
        query = query.where(Ticket.cycle_id == cycle_id)
    if milestone:
        query = query.where(Ticket.milestone == milestone)
    if search:
        term = f"%{search.strip()}%"
        query = query.where(
            (col(Ticket.title).like(term)) | (col(Ticket.external_id).like(term))
        )

    tickets = session.exec(query).all()
    stage_names: dict[str, str] = {}
    orch = OrchestrationService(session)
    for t in tickets:
        template = orch.get_template_for_ticket(t)
        if template and t.workflow_stage_key:
            from loregarden.core.workflow_loader import stage_display_name

            stage_names[t.id] = stage_display_name(template, t.workflow_stage_key)
    return build_tree(session, list(tickets), stage_names=stage_names)


@router.get("", response_model=list[TicketSummary])
def list_tickets(
    *,
    workspace: str | None = None,
    state: TicketState | None = None,
    work_item_type: WorkItemType | None = None,
    parent_ticket_id: str | None = None,
    roots_only: bool = False,
    cycle_id: str | None = None,
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
    if state:
        query = query.where(Ticket.state == state)
    if work_item_type:
        query = query.where(Ticket.work_item_type == work_item_type)
    if parent_ticket_id:
        query = query.where(Ticket.parent_ticket_id == parent_ticket_id)
    if roots_only:
        query = query.where(Ticket.parent_ticket_id.is_(None))  # type: ignore[union-attr]
    if cycle_id:
        query = query.where(Ticket.cycle_id == cycle_id)
    if milestone:
        query = query.where(Ticket.milestone == milestone)
    if search:
        term = f"%{search.strip()}%"
        query = query.where(
            (col(Ticket.title).like(term)) | (col(Ticket.external_id).like(term))
        )
    tickets = session.exec(query.order_by(Ticket.priority, Ticket.created_at)).all()
    return [_ticket_summary(session, t) for t in tickets]


@router.get("/{ticket_id}", response_model=TicketDetail)
def get_ticket(ticket_id: str, session: Session = Depends(get_session)) -> TicketDetail:
    ticket = session.get(Ticket, ticket_id)
    if not ticket:
        raise HTTPException(404, "Ticket not found")
    orch = OrchestrationService(session)
    orch.reconcile_ticket(ticket)
    session.refresh(ticket)
    summary = _ticket_summary(session, ticket)
    return TicketDetail(
        **summary.model_dump(),
        description=ticket.description,
        acceptance_criteria=json.loads(ticket.acceptance_criteria_json or "[]"),
        revision=ticket.revision,
        last_updated_by=ticket.last_updated_by,
        next_agent=ticket.next_agent,
        next_status=ticket.next_status,
        blocking_issues=ticket.blocking_issues,
        state_locked=ticket.state_locked,
        stages=orch.build_stage_views(ticket),
        artifacts=_artifacts_grouped(session, ticket.id),
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


@router.post("/{ticket_id}/start", response_model=TicketDetail)
def start_run(
    ticket_id: str,
    body: StartRunRequest,
    session: Session = Depends(get_session),
) -> TicketDetail:
    ticket = session.get(Ticket, ticket_id)
    if not ticket:
        raise HTTPException(404, "Ticket not found")
    run_svc = RunService(session)
    try:
        run_svc.start_and_execute(ticket, stage_key=body.stage_key)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    session.refresh(ticket)
    return get_ticket(ticket_id, session)


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
