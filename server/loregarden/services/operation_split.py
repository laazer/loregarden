"""Split operation criteria off a code ticket into a parked sibling.

The implementation behind `operation_criteria.split_operation_criteria`. It
lives up here because creating a ticket goes through `TicketService`, which
imports the orchestration modules that run the check, and installs itself on
import the way `run_service` installs the scheduler.

The new ticket is what a person would have made by hand for 782: same parent,
same type and priority, the operation criteria and nothing else, parked
because a person owes it, and related to the ticket it came from.
"""

from __future__ import annotations

from loregarden.models.domain import Ticket, TicketState, UpdateTicketRequest, Workspace
from loregarden.services.acceptance_criteria import load_criteria
from loregarden.services.operation_criteria import set_operation_splitter
from loregarden.services.orchestration import OrchestrationService
from loregarden.services.ticket_relations import TicketRelationService
from loregarden.services.ticket_service import TicketService
from sqlmodel import Session


def _split(session: Session, ticket: Ticket, criteria: list[str]) -> Ticket:
    workspace = session.get(Workspace, ticket.workspace_id)
    if workspace is None:
        raise ValueError(f"Ticket {ticket.external_id} has no workspace to split into")
    sibling = TicketService(session).create_ticket(
        workspace_slug=workspace.slug,
        title=f"Operations split from {ticket.external_id}: {ticket.title}",
        work_item_type=ticket.work_item_type,
        parent_ticket_id=ticket.parent_ticket_id,
        description=(
            f"Split from {ticket.external_id} when its spec passed: these acceptance "
            "criteria change shared state (a published ref, pull requests, other tickets' "
            "rows) rather than code, so a person runs them. The code criteria stayed on "
            f"{ticket.external_id}."
        ),
        acceptance_criteria=criteria,
        priority=ticket.priority,
    )
    orch = OrchestrationService(session)
    orch.update_ticket_manual(sibling, UpdateTicketRequest(state=TicketState.PARKED))
    moved = set(criteria)
    remaining = [c for c in load_criteria(ticket.acceptance_criteria_json) if c not in moved]
    orch.update_ticket_manual(ticket, UpdateTicketRequest(acceptance_criteria=remaining))
    TicketRelationService(session).add_relation(ticket.id, sibling.id, created_by="workflow")
    return sibling


set_operation_splitter(_split)
