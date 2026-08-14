"""Create and manage work items (tickets)."""

from __future__ import annotations

import re
import threading
from datetime import datetime, timezone

from loregarden.core.event_bus import event_bus
from loregarden.models.domain import (
    WORKFLOW_WORK_ITEM_TYPES,
    AgentRun,
    Approval,
    Artifact,
    AutoFixAttempt,
    BtwExchange,
    CIRunResult,
    ConflictReport,
    DomainEvent,
    EventType,
    OrchestrationRun,
    QueuedRun,
    RunMessage,
    RunOutputReview,
    StageFanoutAttempt,
    StageFanoutGroup,
    StageStatus,
    Ticket,
    TicketDependency,
    TicketDiffComment,
    TicketRelation,
    TicketState,
    TicketStudioSession,
    TriageMessage,
    WorkflowInstance,
    WorkItemType,
    Workspace,
)
from loregarden.services.acceptance_criteria import serialize_criteria
from loregarden.services.hierarchy_service import child_count, validate_parent_child
from loregarden.services.orchestration import OrchestrationService
from loregarden.services.workflow_service import resolve_workspace_stages
from loregarden.services.workflow_state import initial_stages_json
from sqlmodel import Session, col, select

# Tables owning a direct `ticket_id` column, deleted (in this order) when a
# ticket is removed. The order is a dependency order — a table that references
# another in this tuple comes first — and `delete_ticket` flushes between them
# so the emitted DELETEs keep it. Anything referencing a ticket and missing here
# outlives the ticket as an orphan row.
_TICKET_OWNED_TABLES = (
    QueuedRun,
    ConflictReport,
    TicketDiffComment,
    DomainEvent,
    TriageMessage,
    Approval,
    RunMessage,
    BtwExchange,
    TicketDependency,
    TicketRelation,
    StageFanoutGroup,
    Artifact,
    AgentRun,
    OrchestrationRun,
    CIRunResult,
    WorkflowInstance,
)

# Edges pointing at a ticket from a column that is not named `ticket_id`. The
# `_TICKET_OWNED_TABLES` sweep matches on `ticket_id` alone, so an inbound edge
# — another ticket depending on this one — is invisible to it.
_TICKET_INBOUND_EDGES = (
    (TicketDependency, TicketDependency.depends_on_ticket_id),
    (TicketRelation, TicketRelation.related_ticket_id),
)


def _slugify(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug[:48] or "work-item"


_external_id_lock = threading.Lock()
_create_ticket_lock = threading.Lock()


def _next_external_id(session: Session, workspace_id: str, title: str) -> str:
    with _external_id_lock:
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
        milestone: str = "",
        external_id: str = "",
        is_integration_review: bool = False,
    ) -> Ticket:
        with _create_ticket_lock:
            return self._create_ticket_impl(
                workspace_slug=workspace_slug,
                title=title,
                work_item_type=work_item_type,
                parent_ticket_id=parent_ticket_id,
                description=description,
                acceptance_criteria=acceptance_criteria,
                priority=priority,
                milestone=milestone,
                external_id=external_id,
                is_integration_review=is_integration_review,
            )

    def _create_ticket_impl(
        self,
        *,
        workspace_slug: str,
        title: str,
        work_item_type: WorkItemType,
        parent_ticket_id: str | None = None,
        description: str = "",
        acceptance_criteria: list[str] | None = None,
        priority: int = 3,
        milestone: str = "",
        external_id: str = "",
        is_integration_review: bool = False,
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
            acceptance_criteria_json=serialize_criteria(acceptance_criteria),
            is_integration_review=is_integration_review,
            last_updated_by="user",
        )

        template, stages = resolve_workspace_stages(self.session, ws)
        if work_item_type in WORKFLOW_WORK_ITEM_TYPES:
            if not template or not stages:
                raise ValueError("Workspace has no workflow template for executable work items")
            first_stage = min(stages, key=lambda s: s.order)
            ticket.workflow_stage_key = first_stage.key
            ticket.workflow_stage_status = StageStatus.PENDING
            ticket.next_agent = first_stage.agent_id

        self.session.add(ticket)
        ticket_id = ticket.id
        self.session.commit()
        ticket = self.session.get(Ticket, ticket_id) or ticket

        if work_item_type in WORKFLOW_WORK_ITEM_TYPES and template:
            instance = WorkflowInstance(
                ticket_id=ticket.id,
                template_id=template.id,
                template_version=template.version,
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
        ticket_id = ticket.id
        self.session.commit()
        return self.session.get(Ticket, ticket_id) or ticket

    def _delete_grandchildren(self, ticket_id: str) -> None:
        """Rows reaching the ticket through one of its children, not directly.

        They carry no `ticket_id` of their own, so the `_TICKET_OWNED_TABLES`
        sweep cannot see them — and each would outlive the child it hangs off.
        """
        agent_run_ids = self.session.exec(
            select(AgentRun.id).where(AgentRun.ticket_id == ticket_id)
        ).all()
        for review in self.session.exec(
            select(RunOutputReview).where(col(RunOutputReview.run_id).in_(agent_run_ids))
        ).all():
            self.session.delete(review)

        ci_run_result_ids = self.session.exec(
            select(CIRunResult.id).where(CIRunResult.ticket_id == ticket_id)
        ).all()
        for attempt in self.session.exec(
            select(AutoFixAttempt).where(
                col(AutoFixAttempt.ci_run_result_id).in_(ci_run_result_ids)
            )
        ).all():
            self.session.delete(attempt)

        fanout_group_ids = self.session.exec(
            select(StageFanoutGroup.id).where(StageFanoutGroup.ticket_id == ticket_id)
        ).all()
        for attempt in self.session.exec(
            select(StageFanoutAttempt).where(col(StageFanoutAttempt.group_id).in_(fanout_group_ids))
        ).all():
            self.session.delete(attempt)

    def _delete_owned_rows(self, ticket_id: str) -> None:
        """Every row that names this ticket, in an order SQLite would accept."""
        for studio_session in self.session.exec(
            select(TicketStudioSession).where(TicketStudioSession.parent_ticket_id == ticket_id)
        ).all():
            # Kept, not deleted: a studio session outlives the ticket it drafted.
            studio_session.parent_ticket_id = None
            self.session.add(studio_session)

        for model, column in _TICKET_INBOUND_EDGES:
            for row in self.session.exec(select(model).where(column == ticket_id)).all():
                self.session.delete(row)

        # Flush per table rather than once at the end: within a single flush the
        # unit of work orders statements by mapper dependency, and these tables
        # are joined by raw foreign keys with no ORM relationship to derive that
        # order from — it emitted `DELETE FROM tickets` ahead of the child rows.
        for model in _TICKET_OWNED_TABLES:
            for row in self.session.exec(select(model).where(model.ticket_id == ticket_id)).all():
                self.session.delete(row)
            self.session.flush()

    def delete_ticket(self, ticket_id: str) -> None:
        ticket = self.session.get(Ticket, ticket_id)
        if not ticket:
            raise ValueError("Ticket not found")
        if child_count(self.session, ticket_id) > 0:
            raise ValueError("Delete or reassign child work items before deleting this ticket")

        self._delete_grandchildren(ticket_id)
        self._delete_owned_rows(ticket_id)
        self.session.delete(ticket)
        self.session.commit()
