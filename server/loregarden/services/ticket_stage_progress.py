"""How far through its workflow a ticket is, as stages settled out of stages total.

The sidebar's Running rows draw it as a ring, so a lane reads as "4 of 9" rather
than a dot that only says something is running.
"""

from __future__ import annotations

from dataclasses import dataclass

from loregarden.models.domain import StageStatus, Ticket
from loregarden.services.workflow_service import resolve_ticket_stages, workflow_instance_for
from loregarden.services.workflow_state import parse_stage_map
from sqlmodel import Session

#: A stage that will not run again: done, or closed without running.
SETTLED = frozenset({StageStatus.DONE, StageStatus.WONT_DO})


@dataclass(frozen=True)
class StageProgress:
    done: int
    total: int


def stage_progress(session: Session, ticket: Ticket) -> StageProgress | None:
    """None when the ticket has no workflow instance yet, so there is nothing to count."""
    instance = workflow_instance_for(session, ticket.id)
    if instance is None:
        return None
    _, stages = resolve_ticket_stages(session, ticket)
    if not stages:
        return None
    statuses = list(parse_stage_map(instance, stages).values())
    return StageProgress(done=sum(status in SETTLED for status in statuses), total=len(statuses))
