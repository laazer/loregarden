"""Detectors for findings that ended up where they make their source a parent.

A ticket filed under the ticket a run was working on made that ticket a
parent, and a parent never runs its own stages (lg-milestone-that-2604). Two
shapes reach that state, one per writer:

- a `follow_up` decision left the finding under its source, where it is an
  owned child the source waits for;
- a ticket was created under the run's ticket with no discovery row, so it
  was never held out as a finding at all (lg-milestone-that-2605: the create
  committed, then its dependency was refused, and the row was never written).

Both are report-only: whether the finding moves, becomes the source's own
work, or is dropped is a scope decision.
"""

from __future__ import annotations

from loregarden.agents.registry import REPAIR_AGENT_ID
from loregarden.core.state_machine import StateMachine
from loregarden.models.domain import AgentRun, FindingDecision, Ticket, TicketDiscovery
from loregarden.services.invariants import Violation
from loregarden.services.ticket_studio_service import TICKET_STUDIO_AGENT_ID
from sqlalchemy import and_, func
from sqlalchemy.orm import aliased
from sqlmodel import Session, col, select

#: Runs whose job is to put tickets under the one they run on: the scoper's
#: decomposition and the repair agent's filings under a milestone.
DECOMPOSING_AGENTS = (TICKET_STUDIO_AGENT_ID, REPAIR_AGENT_ID)

_OPEN = col(Ticket.state).not_in(StateMachine.TERMINAL_TICKET_STATES)


def follow_ups_under_their_source(session: Session) -> list[Violation]:
    """Open follow-ups still parented under an open source.

    `entered_at` is the decision's time: the row records no time for the move.
    """
    source = aliased(Ticket)
    rows = session.exec(
        select(Ticket, source, TicketDiscovery)
        .join(TicketDiscovery, col(TicketDiscovery.filed_ticket_id) == Ticket.id)
        .join(source, col(source.id) == TicketDiscovery.source_ticket_id)
        .where(
            TicketDiscovery.decision == FindingDecision.FOLLOW_UP,
            col(Ticket.parent_ticket_id) == source.id,
            _OPEN,
            col(source.state).not_in(StateMachine.TERMINAL_TICKET_STATES),
        )
    ).all()
    return [
        Violation(
            subject_id=finding.id,
            ticket_id=src.id,
            entered_at=discovery.decided_at,
            detail=(
                f"{finding.external_id} was decided follow_up but is still under its source "
                f"{src.external_id}, which can no longer run its own stages"
            ),
        )
        for finding, src, discovery in rows
    ]


def unrecorded_children_of_a_run(session: Session) -> list[Violation]:
    """Open tickets created under a ticket while a non-decomposing run was on it, with no finding row.

    `entered_at` is the ticket's creation.
    """
    parent = aliased(Ticket)
    rows = session.exec(
        select(Ticket, parent, AgentRun)
        .join(parent, col(parent.id) == Ticket.parent_ticket_id)
        .join(
            AgentRun,
            and_(
                col(AgentRun.ticket_id) == parent.id,
                col(AgentRun.started_at) <= Ticket.created_at,
                func.coalesce(AgentRun.finished_at, Ticket.created_at) >= Ticket.created_at,
            ),
        )
        .outerjoin(TicketDiscovery, col(TicketDiscovery.filed_ticket_id) == Ticket.id)
        .where(
            col(TicketDiscovery.id).is_(None),
            col(AgentRun.agent_id).not_in(DECOMPOSING_AGENTS),
            _OPEN,
        )
    ).all()
    seen: dict[str, Violation] = {}
    for child, owner, run in rows:
        seen.setdefault(
            child.id,
            Violation(
                subject_id=child.id,
                ticket_id=owner.id,
                entered_at=child.created_at,
                detail=(
                    f"{child.external_id} was created under {owner.external_id} during its "
                    f"{run.agent_id} run ({run.run_code}) with no finding row, so "
                    f"{owner.external_id} became a parent without a scope decision"
                ),
            ),
        )
    return list(seen.values())
