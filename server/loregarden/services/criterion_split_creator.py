"""The ticket creator behind `criterion_split`, installed on import.

It lives up here because `TicketService` imports the orchestration modules
that call `criterion_split` (see `operation_split`, the same wiring).
"""

from __future__ import annotations

from loregarden.models.domain import Ticket, WorkItemType, Workspace
from loregarden.services.criterion_split import SPLIT_BY, set_split_ticket_creator
from loregarden.services.ticket_service import TicketService
from sqlmodel import Session


def _create_split_ticket(
    session: Session,
    source: Ticket,
    parent_id: str,
    item_type: WorkItemType,
    title: str,
    description: str,
    criteria: list[str],
) -> Ticket:
    workspace = session.get(Workspace, source.workspace_id)
    return TicketService(session).create_ticket(
        workspace_slug=workspace.slug if workspace else None,
        title=title,
        work_item_type=item_type,
        parent_ticket_id=parent_id,
        description=description,
        acceptance_criteria=criteria,
        priority=source.priority,
        created_by=SPLIT_BY,
    )


set_split_ticket_creator(_create_split_ticket)
