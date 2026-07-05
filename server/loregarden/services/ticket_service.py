"""Create and manage work items (tickets)."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone

from sqlmodel import Session, select

from loregarden.core.event_bus import event_bus
from loregarden.models.domain import (
    EventType,
    StageStatus,
    Ticket,
    TicketState,
    WorkItemType,
    WorkflowInstance,
    Workspace,
)
from loregarden.services.hierarchy_service import validate_parent_child
from loregarden.services.orchestration import OrchestrationService
from loregarden.services.workflow_service import resolve_workspace_stages
from loregarden.services.workflow_state import initial_stages_json


def _slugify(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug[:48] or "work-item"


def _next_external_id(session: Session, workspace_id: str, title: str) -> str:
    existing = {
        t.external_id
        for t in session.exec(select(Ticket).where(Ticket.workspace_id == workspace_id)).all()
    }
    count = len(existing) + 1
    base = f"{count:02d}-{_slugify(title)}"
    candidate = base
    suffix = 2
    while candidate in existing:
        candidate = f"{base}-{suffix}"
        suffix += 1
    return candidate


def _required_parent_type(child_type: WorkItemType) -> WorkItemType | None:
    for parent_type, allowed in {
        WorkItemType.MILESTONE: [WorkItemType.FEATURE],
        WorkItemType.FEATURE: [WorkItemType.CAPABILITY],
        WorkItemType.CAPABILITY: [WorkItemType.TASK, WorkItemType.BUG],
    }.items():
        if child_type in allowed:
            return parent_type
    return None


class TicketService:
    def __init__(self, session: Session) -> None:
        self.session = session

    def create_ticket(
        self,
        *,
        workspace_slug: str,
        title: str,
        work_item_type: WorkItemType,
        parent_ticket_id: str | None = None,
        description: str = "",
        acceptance_criteria: list[str] | None = None,
        priority: int = 3,
        cycle_id: str | None = None,
        milestone: str = "",
        branch: str = "",
        external_id: str = "",
    ) -> Ticket:
        title = title.strip()
        if not title:
            raise ValueError("Title is required")

        ws = self.session.exec(select(Workspace).where(Workspace.slug == workspace_slug)).first()
        if not ws:
            raise ValueError(f"Workspace not found: {workspace_slug}")

        if priority < 1 or priority > 3:
            raise ValueError("Priority must be between 1 and 3")

        parent: Ticket | None = None
        required_parent = _required_parent_type(work_item_type)

        if work_item_type == WorkItemType.MILESTONE:
            if parent_ticket_id:
                raise ValueError("Milestones cannot have a parent")
        elif not parent_ticket_id:
            raise ValueError(f"{work_item_type.value} requires a parent work item")
        else:
            parent = self.session.get(Ticket, parent_ticket_id)
            if not parent or parent.workspace_id != ws.id:
                raise ValueError("Parent work item not found in workspace")
            validate_parent_child(parent.work_item_type, work_item_type)
            if required_parent and parent.work_item_type != required_parent:
                raise ValueError(
                    f"{work_item_type.value} must be created under a {required_parent.value}"
                )

        ext_id = external_id.strip() or _next_external_id(self.session, ws.id, title)
        dup = self.session.exec(
            select(Ticket).where(
                Ticket.workspace_id == ws.id,
                Ticket.external_id == ext_id,
            )
        ).first()
        if dup:
            raise ValueError(f"external_id already exists: {ext_id}")

        inherited_milestone = milestone.strip() or (parent.milestone if parent else "")
        inherited_cycle = cycle_id or (parent.cycle_id if parent else None)

        ticket = Ticket(
            external_id=ext_id,
            workspace_id=ws.id,
            title=title,
            description=description.strip(),
            state=TicketState.BACKLOG,
            priority=priority,
            milestone=inherited_milestone,
            work_item_type=work_item_type,
            parent_ticket_id=parent_ticket_id,
            cycle_id=inherited_cycle,
            branch=branch.strip(),
            acceptance_criteria_json=json.dumps(
                [line.strip() for line in (acceptance_criteria or []) if line.strip()]
            ),
            last_updated_by="user",
        )

        template, stages = resolve_workspace_stages(self.session, ws)
        if work_item_type in (WorkItemType.TASK, WorkItemType.BUG):
            if not template or not stages:
                raise ValueError("Workspace has no workflow template for executable work items")
            first_stage = min(stages, key=lambda s: s.order)
            ticket.workflow_stage_key = first_stage.key
            ticket.workflow_stage_status = StageStatus.PENDING
            ticket.next_agent = first_stage.agent_id

        self.session.add(ticket)
        self.session.commit()
        self.session.refresh(ticket)

        if work_item_type in (WorkItemType.TASK, WorkItemType.BUG) and template:
            instance = WorkflowInstance(
                ticket_id=ticket.id,
                template_id=template.id,
                current_stage_key=ticket.workflow_stage_key,
                stages_json=initial_stages_json(stages),
            )
            self.session.add(instance)
            self.session.commit()
            OrchestrationService(self.session).reconcile_ticket(ticket)

        event_bus.publish(
            self.session,
            EventType.TICKET_CREATED,
            workspace_id=ws.id,
            ticket_id=ticket.id,
            payload={
                "external_id": ticket.external_id,
                "work_item_type": work_item_type.value,
            },
        )
        ticket.updated_at = datetime.now(timezone.utc)
        self.session.add(ticket)
        self.session.commit()
        self.session.refresh(ticket)
        return ticket
