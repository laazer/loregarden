"""Instances on a template, for tests of migrations that reshape one."""

from __future__ import annotations

import json

from loregarden.core.workflow_loader import sync_workflow_templates
from loregarden.models.domain import (
    Ticket,
    TicketState,
    WorkflowInstance,
    WorkflowTemplate,
    WorkflowTemplateVersion,
    Workspace,
)
from loregarden.services.orchestration import OrchestrationService
from loregarden.services.workflow_routing import apply_stage_route
from loregarden.services.workflow_service import (
    resolve_ticket_stages,
    resolve_ticket_transitions,
)
from sqlmodel import Session, select
from tests.factories import make_ticket, make_workspace


def template(session: Session, slug: str) -> WorkflowTemplate:
    sync_workflow_templates(session)
    return session.exec(select(WorkflowTemplate).where(WorkflowTemplate.slug == slug)).one()


def forget_template(session: Session, slug: str) -> None:
    """Drop a template and its history.

    The seeded test database has run every migration, so a test of one that
    creates or reshapes a template first puts the row back to before it.
    """
    row = session.exec(select(WorkflowTemplate).where(WorkflowTemplate.slug == slug)).first()
    if row is None:
        return
    for version in session.exec(
        select(WorkflowTemplateVersion).where(WorkflowTemplateVersion.template_id == row.id)
    ).all():
        session.delete(version)
    session.flush()  # no ORM relationship orders these two deletes
    session.delete(row)
    session.commit()


def bound_workspace(session: Session, slug: str, template_slug: str) -> Workspace:
    workspace = make_workspace(session, slug=slug)
    workspace.workflow_template_id = template(session, template_slug).id
    session.add(workspace)
    session.commit()
    return workspace


def ticket_on(
    session: Session,
    workspace: Workspace,
    name: str,
    *,
    state: TicketState,
    stage_key: str,
    statuses: dict[str, str] | None = None,
    pinned: bool = True,
) -> Ticket:
    """A ticket sitting on `stage_key`, with a stage list of its template's shape."""
    row = session.get(WorkflowTemplate, workspace.workflow_template_id)
    keys = [stage["key"] for stage in json.loads(row.stages_json)]
    ticket = make_ticket(
        session,
        workspace_id=workspace.id,
        external_id=f"{workspace.slug}-{name}",
        state=state,
    )
    ticket.workflow_stage_key = stage_key
    session.add(ticket)
    session.add(
        WorkflowInstance(
            ticket_id=ticket.id,
            template_id=row.id,
            template_version=row.version if pinned else None,
            current_stage_key=stage_key,
            stages_json=json.dumps(
                [{"key": k, "status": (statuses or {}).get(k, "pending")} for k in keys]
            ),
        )
    )
    session.commit()
    return ticket


def instance_of(session: Session, ticket: Ticket) -> WorkflowInstance:
    return session.exec(
        select(WorkflowInstance).where(WorkflowInstance.ticket_id == ticket.id)
    ).one()


def instance_keys(session: Session, ticket: Ticket) -> list[str]:
    return [entry["key"] for entry in json.loads(instance_of(session, ticket).stages_json)]


def current_stage_after_a_read(session: Session, ticket: Ticket) -> str:
    """The cursor once the ticket page has reconciled it; asserts it is a real stage."""
    OrchestrationService(session).reconcile_ticket(ticket)
    session.refresh(ticket)
    _, stages = resolve_ticket_stages(session, ticket)
    assert ticket.workflow_stage_key in {stage.key for stage in stages}
    return ticket.workflow_stage_key


def pass_stage(session: Session, ticket: Ticket, stage_key: str) -> str:
    """Pass `stage_key` the way a finished run does; the stage the route named."""
    _, stages = resolve_ticket_stages(session, ticket)
    plan = apply_stage_route(
        ticket,
        instance_of(session, ticket),
        stages,
        resolve_ticket_transitions(session, ticket),
        from_key=stage_key,
    )
    session.commit()
    assert ticket.workflow_stage_key in {stage.key for stage in stages}
    return plan.to_key
