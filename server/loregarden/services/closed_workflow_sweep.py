"""Closed tickets whose workflow still says work is owed, found and closed.

`db.closed_workflow_guard` closes the stages at flush; this is the invariant
`closed_tickets_have_closed_workflows` catching a row that got past it, or that
predates it (361 of 466 closed tickets on the live database when it landed).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime

from loregarden.db.closed_workflow_guard import (
    CLOSED_STATES,
    OPEN_STAGE_STATUSES,
    open_stage_keys,
    settle_closed_workflow,
)
from loregarden.models.domain import Ticket, WorkflowInstance
from sqlmodel import Session, col, select

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class OpenWorkflow:
    ticket: Ticket
    instance: WorkflowInstance
    open_stages: list[str]

    @property
    def entered_at(self) -> datetime | None:
        """The later of the close and the last stage-map write.

        Either one can be the write that made the pair disagree: closing over
        open stages, or reopening stages under a closed ticket. Taking the
        later errs towards reporting a re-entry, never towards hiding one.
        """
        stamps = [s for s in (self.ticket.resolved_at, self.instance.updated_at) if s]
        return max(stamps) if stamps else None


def open_workflows(session: Session) -> list[OpenWorkflow]:
    """Closed tickets with an open stage, or a cursor still showing one."""
    rows = session.exec(
        select(Ticket, WorkflowInstance)
        .join(WorkflowInstance, col(WorkflowInstance.ticket_id) == col(Ticket.id))
        .where(col(Ticket.state).in_(CLOSED_STATES))
    ).all()
    found: list[OpenWorkflow] = []
    for ticket, instance in rows:
        keys = open_stage_keys(instance.stages_json)
        if keys or ticket.workflow_stage_status in OPEN_STAGE_STATUSES:
            found.append(OpenWorkflow(ticket, instance, keys))
    return found


def close_workflows(session: Session, workflows: list[OpenWorkflow]) -> int:
    """Close each workflow; how many now agree with their ticket."""
    repaired = 0
    for item in workflows:
        settle_closed_workflow(item.ticket, item.instance)
        session.add(item.ticket)
        session.add(item.instance)
        if (
            not open_stage_keys(item.instance.stages_json)
            and item.ticket.workflow_stage_status not in OPEN_STAGE_STATUSES
        ):
            repaired += 1
    if workflows:
        session.commit()
        logger.warning(
            "Closed the open stages of %d closed ticket(s): %s",
            len(workflows),
            ", ".join(item.ticket.external_id for item in workflows[:20]),
        )
    return repaired
