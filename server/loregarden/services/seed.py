import json

from loregarden.core.event_bus import event_bus
from loregarden.core.workflow_loader import sync_workflow_templates
from loregarden.models.domain import (
    EventType,
    StageStatus,
    Ticket,
    TicketState,
    WorkflowInstance,
    WorkflowTemplate,
    WorkItemType,
    Workspace,
)
from loregarden.services.orchestration import OrchestrationService
from loregarden.services.workflow_service import resolve_workspace_stages
from loregarden.services.workflow_state import stages_up_to_done_json
from sqlmodel import Session, select

BOOTSTRAP_TASKS = [
    {
        "external_id": "01-bootstrap-fastapi-control-plane",
        "title": "Bootstrap FastAPI control plane with SQLite state engine",
        "description": (
            "Implement the loregarden backend: models, event bus, workflow engine, "
            "and API routes for tickets, runs, inbox, and events."
        ),
        "state": TicketState.IN_PROGRESS,
        "priority": 1,
        "stage_key": "implementation",
        "stage_status": StageStatus.DONE,
        "capability_key": "api-core",
        "acceptance_criteria": [
            "FastAPI app serves ticket and workspace endpoints",
            "SQLite stores tickets, workflows, runs, artifacts, approvals, events",
            "Backend owns all state transitions",
            "pytest covers orchestration service",
        ],
    },
    {
        "external_id": "02-bootstrap-react-ide-shell",
        "title": "Bootstrap React IDE shell matching design mockup",
        "description": (
            "Three-pane UI: workspaces/tickets sidebar, dual-state workflow center, "
            "artifact viewer. TanStack Query fetches backend truth only."
        ),
        "state": TicketState.IN_PROGRESS,
        "priority": 1,
        "stage_key": "implementation",
        "stage_status": StageStatus.DONE,
        "capability_key": "ui-shell",
        "acceptance_criteria": [
            "UI renders ticket list and selected ticket workflow",
            "Artifact tabs show diff/logs/tests/context from API",
            "Approval inbox slide-over wired to /inbox/approvals",
            "No client-side workflow state machine logic",
        ],
    },
    {
        "external_id": "03-wire-cli-agent-runner",
        "title": "Wire CLI agent runner for stage execution",
        "description": (
            "Spawn local CLI agents via subprocess for each workflow stage. "
            "Runs emit artifacts and domain events."
        ),
        "state": TicketState.IN_PROGRESS,
        "priority": 2,
        "stage_key": "implementation",
        "stage_status": StageStatus.DONE,
        "capability_key": "agent-runtime",
        "acceptance_criteria": [
            "Start run invokes agent registry + CLI executor",
            "Run completion updates ticket workflow stage status",
            "Failed runs set ticket to blocked with stderr evidence",
        ],
    },
    {
        "external_id": "04-workflow-template-overrides",
        "title": "Per-workspace workflow template overrides",
        "description": (
            "Layer DB templates seeded from agent_context/workflows YAML with "
            "per-workspace overrides for stage gating."
        ),
        "state": TicketState.IN_PROGRESS,
        "priority": 2,
        "stage_key": "implementation",
        "stage_status": StageStatus.DONE,
        "capability_key": "workflow-config",
        "acceptance_criteria": [
            "Workflow templates load from agent_context/workflows YAML",
            "Workspace can select template at creation",
            "UI stage stepper reflects template stages",
        ],
    },
    {
        "external_id": "05-self-tracking-milestone",
        "title": "Enable self-tracking via agent_context + project_board bootstrap",
        "description": (
            "Ship agent_context agents/skills and milestone folder structure. "
            "Tickets authoritative in SQLite; optional markdown export for agents."
        ),
        "state": TicketState.DONE,
        "priority": 3,
        "stage_key": "implementation",
        "stage_status": StageStatus.DONE,
        "capability_key": "self-tracking",
        "acceptance_criteria": [
            "agent_context/agents and skills present",
            "project_board/01_milestone_bootstrap mirrors seeded tickets",
            "Markdown export endpoint or script for agent consumption",
        ],
    },
]

HIERARCHY = {
    "milestone": {
        "external_id": "m01-bootstrap",
        "title": "M01 — Bootstrap vertical slice",
        "children": {
            "backend-platform": {
                "title": "Backend platform",
                "children": {
                    "api-core": {"title": "API & state engine"},
                    "agent-runtime": {"title": "CLI agent runtime"},
                },
            },
            "ide-experience": {
                "title": "IDE experience",
                "children": {
                    "ui-shell": {"title": "React IDE shell"},
                    "workflow-config": {"title": "Workflow templates"},
                    "self-tracking": {"title": "Self-tracking & export"},
                },
            },
        },
    },
}


def _add_ticket(
    session: Session,
    *,
    ws: Workspace,
    external_id: str,
    title: str,
    work_item_type: WorkItemType,
    parent_ticket_id: str | None = None,
    description: str = "",
    state: TicketState = TicketState.BACKLOG,
    priority: int = 3,
    milestone: str = "01_milestone_bootstrap",
    **kwargs,
) -> Ticket:
    ticket = Ticket(
        external_id=external_id,
        workspace_id=ws.id,
        title=title,
        description=description,
        state=state,
        priority=priority,
        milestone=milestone,
        work_item_type=work_item_type,
        parent_ticket_id=parent_ticket_id,
        last_updated_by="seed",
        **kwargs,
    )
    session.add(ticket)
    session.commit()
    session.refresh(ticket)
    event_bus.publish(
        session,
        EventType.TICKET_CREATED,
        workspace_id=ws.id,
        ticket_id=ticket.id,
        payload={"external_id": ticket.external_id, "work_item_type": work_item_type.value},
    )
    return ticket


def _seed_hierarchy(
    session: Session,
    ws: Workspace,
    loregarden_tpl: WorkflowTemplate | None,
) -> dict[str, str]:
    """Create milestone → feature → capability containers; return capability id map."""
    capability_ids: dict[str, str] = {}
    root = HIERARCHY["milestone"]
    milestone = _add_ticket(
        session,
        ws=ws,
        external_id=root["external_id"],
        title=root["title"],
        work_item_type=WorkItemType.MILESTONE,
        state=TicketState.DONE,
        priority=1,
    )
    for feat_key, feat in root["children"].items():
        feature = _add_ticket(
            session,
            ws=ws,
            external_id=f"m01-{feat_key}",
            title=feat["title"],
            work_item_type=WorkItemType.FEATURE,
            parent_ticket_id=milestone.id,
            state=TicketState.DONE,
            priority=2,
        )
        for cap_key, cap in feat["children"].items():
            capability = _add_ticket(
                session,
                ws=ws,
                external_id=f"m01-{feat_key}-{cap_key}",
                title=cap["title"],
                work_item_type=WorkItemType.CAPABILITY,
                parent_ticket_id=feature.id,
                state=TicketState.DONE,
                priority=2,
            )
            capability_ids[cap_key] = capability.id
    return capability_ids


def _ensure_hierarchy(session: Session, ws: Workspace) -> None:
    """Backfill hierarchy for databases seeded before tree support."""
    milestone = session.exec(
        select(Ticket).where(
            Ticket.workspace_id == ws.id,
            Ticket.work_item_type == WorkItemType.MILESTONE,
        )
    ).first()
    if milestone:
        return

    loregarden_tpl = session.exec(
        select(WorkflowTemplate).where(WorkflowTemplate.slug == "loregarden-tdd")
    ).first()
    capability_ids = _seed_hierarchy(session, ws, loregarden_tpl)

    for item in BOOTSTRAP_TASKS:
        ticket = session.exec(
            select(Ticket).where(
                Ticket.workspace_id == ws.id,
                Ticket.external_id == item["external_id"],
            )
        ).first()
        if not ticket:
            continue
        ticket.work_item_type = WorkItemType.TASK
        ticket.parent_ticket_id = capability_ids.get(item["capability_key"])
        ticket.milestone = "01_milestone_bootstrap"
        session.add(ticket)
    session.commit()
    _reconcile_task_workflows(session, ws)


def _reconcile_task_workflows(session: Session, ws: Workspace) -> None:
    orch = OrchestrationService(session)
    tickets = session.exec(
        select(Ticket).where(
            Ticket.workspace_id == ws.id,
            Ticket.work_item_type == WorkItemType.TASK,
        )
    ).all()
    for ticket in tickets:
        instance = session.exec(
            select(WorkflowInstance).where(WorkflowInstance.ticket_id == ticket.id)
        ).first()
        if instance:
            orch.reconcile_ticket(ticket)


def seed_database(session: Session) -> None:
    templates = sync_workflow_templates(session)
    loregarden_tpl = session.exec(
        select(WorkflowTemplate).where(WorkflowTemplate.slug == "loregarden-tdd")
    ).first()
    if not loregarden_tpl and templates:
        loregarden_tpl = templates[0]

    ws = session.exec(select(Workspace).where(Workspace.slug == "loregarden")).first()
    if not ws:
        ws = Workspace(
            slug="loregarden",
            name="loregarden",
            repo_path=".",
            workflow_template_id=loregarden_tpl.id if loregarden_tpl else None,
        )
        session.add(ws)
        session.commit()
        session.refresh(ws)
    elif loregarden_tpl and not ws.workflow_template_id:
        ws.workflow_template_id = loregarden_tpl.id
        session.add(ws)
        session.commit()

    existing = session.exec(select(Ticket).where(Ticket.workspace_id == ws.id)).first()
    if existing:
        _ensure_hierarchy(session, ws)
        _reconcile_task_workflows(session, ws)
        return

    capability_ids = _seed_hierarchy(session, ws, loregarden_tpl)

    for item in BOOTSTRAP_TASKS:
        ticket = _add_ticket(
            session,
            ws=ws,
            external_id=item["external_id"],
            title=item["title"],
            description=item["description"],
            work_item_type=WorkItemType.TASK,
            parent_ticket_id=capability_ids.get(item["capability_key"]),
            state=item["state"],
            priority=item["priority"],
            acceptance_criteria_json=json.dumps(item["acceptance_criteria"]),
            workflow_stage_key=item["stage_key"],
            workflow_stage_status=item["stage_status"],
            next_agent="frontend_implementer" if item["state"] == TicketState.IN_PROGRESS else "",
        )

        if loregarden_tpl:
            _, stages = resolve_workspace_stages(session, ws)
            instance = WorkflowInstance(
                ticket_id=ticket.id,
                template_id=loregarden_tpl.id,
                current_stage_key=item["stage_key"],
                stages_json=stages_up_to_done_json(stages, item["stage_key"]),
            )
            session.add(instance)
            session.commit()
            OrchestrationService(session).reconcile_ticket(ticket)

    in_progress = session.exec(
        select(Ticket).where(
            Ticket.workspace_id == ws.id,
            Ticket.external_id == "04-workflow-template-overrides",
        )
    ).first()
    if in_progress:
        from loregarden.models.domain import Approval, ApprovalStatus

        approval = Approval(
            ticket_id=in_progress.id,
            workspace_id=ws.id,
            title="Approve workflow template override before merge",
            level="medium",
            stage_key="review",
            impact="Template switch affects stage gating for all workspace tickets.",
            status=ApprovalStatus.PENDING,
        )
        session.add(approval)
        session.commit()
        event_bus.publish(
            session,
            EventType.APPROVAL_REQUESTED,
            workspace_id=ws.id,
            ticket_id=in_progress.id,
            payload={"approval_id": approval.id},
        )

        from loregarden.models.domain import Artifact

        artifact = Artifact(
            ticket_id=in_progress.id,
            kind="diff",
            title="client/src/pages/Dashboard.tsx",
            content_json=json.dumps(
                {
                    "file": "client/src/pages/Dashboard.tsx",
                    "add": "+48",
                    "del": "−6",
                    "files": "3 files changed",
                    "lines": [
                        {
                            "type": "a",
                            "ln": "1",
                            "text": "workflowTemplates query + template selector",
                        },
                        {
                            "type": "a",
                            "ln": "2",
                            "text": "workspaceWorkflow badge in workflow pane",
                        },
                        {"type": "c", "ln": "3", "text": "ArtifactView runs list in context tab"},
                    ],
                }
            ),
        )
        session.add(artifact)
        session.commit()
