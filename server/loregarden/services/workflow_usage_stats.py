"""How much each workflow template is used: Workflow Studio's index.

A ticket uses a template through its `workflow_instances` row. One grouped
query counts them per template, and how many of those tickets are still open
(not done or won't do), so the index can tell the templates carrying the work
from the ones that only carried it once.
"""

from datetime import datetime

from loregarden.models.domain import Ticket, TicketState, WorkflowInstance, WorkflowTemplate
from pydantic import BaseModel
from sqlalchemy import case, func
from sqlmodel import Session, col, select

CLOSED = (TicketState.DONE, TicketState.WONT_DO)


class WorkflowUsageStats(BaseModel):
    template_slug: str
    #: Tickets that have run on this template, open or closed.
    tickets: int
    #: Of those, the ones not yet done or won't do.
    open: int
    #: When a ticket last started on it (naive UTC, as stored); null if none has.
    last_used_at: datetime | None


def workflow_usage_stats(session: Session) -> list[WorkflowUsageStats]:
    """Every template, including the ones no ticket has used."""
    is_open = (col(Ticket.id).is_not(None)) & col(Ticket.state).not_in(CLOSED)
    stmt = (
        select(
            WorkflowTemplate.slug,
            func.count(col(WorkflowInstance.id)),
            func.sum(case((is_open, 1), else_=0)),
            func.max(WorkflowInstance.created_at),
        )
        .select_from(WorkflowTemplate)
        .outerjoin(WorkflowInstance, col(WorkflowInstance.template_id) == WorkflowTemplate.id)
        .outerjoin(Ticket, col(Ticket.id) == WorkflowInstance.ticket_id)
        .group_by(WorkflowTemplate.id)
    )
    return [
        WorkflowUsageStats(template_slug=slug, tickets=tickets, open=open_ or 0, last_used_at=last)
        for slug, tickets, open_, last in session.exec(stmt).all()
    ]
