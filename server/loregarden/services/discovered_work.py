"""Findings a run filed: which of them are a parent's work, and recording a decision.

See `models.domain.discovery_tables`. This module answers "is this ticket part
of its parent's work?" for every reader that walks a parent's children (the
child pass, the rollup, the terminal sweep, the integration review, the
external harness, the autopilot), so they all answer it the same way:

- `owned_children`: a parent's children, minus every finding that is not a
  follow-up.
- `refuse_undecided`: the guard at `start_orchestration_run`, the one writer
  every dispatcher shares. A finding is not work until it is decided.
- `undecided_findings_of`: what a ticket's spec owner still has to decide.

The decision itself, and the round that asks for it, are `scope_round`'s.
"""

from __future__ import annotations

from loregarden.core.state_machine import StateMachine
from loregarden.models.domain import AgentRun, FindingDecision, Ticket, TicketDiscovery
from sqlalchemy import or_
from sqlmodel import Session, col, select


class UndecidedFinding(ValueError):
    """A dispatcher tried to start a finding whose scope nobody has decided.

    A `ValueError`, so every start path that already turns a refusal into a
    400 or an abandoned claim does the same with this one.
    """

    def __init__(self, ticket: Ticket, discovery: TicketDiscovery) -> None:
        super().__init__(
            f"{ticket.external_id} is a finding a run filed, and its scope has not been "
            "decided. Its source ticket's spec stage decides it (loregarden_decide_finding); "
            "it cannot be started before then."
        )
        self.discovery = discovery


def held_out_ids():
    """A subquery of findings that are not their parent's work: all but follow-ups.

    Undecided and deferred ones are not work here yet. Amended and dropped ones
    never were: an amendment became a criterion of its source, and a drop is
    not work at all. Counting their closed tickets as children would roll a
    source the finding was filed *under* up to ``done`` on the spot.
    """
    return select(TicketDiscovery.filed_ticket_id).where(
        or_(
            col(TicketDiscovery.decision).is_(None),
            TicketDiscovery.decision != FindingDecision.FOLLOW_UP,
        )
    )


def owned_children(parent_id: str):
    """A ``select(Ticket)`` of the children that are the parent's work."""
    return select(Ticket).where(
        Ticket.parent_ticket_id == parent_id, col(Ticket.id).not_in(held_out_ids())
    )


def has_owned_children(session: Session, parent_id: str) -> bool:
    return session.exec(owned_children(parent_id).limit(1)).first() is not None


def undecided(session: Session, ticket_id: str) -> TicketDiscovery | None:
    """The ticket's finding row while its scope is undecided; None otherwise."""
    return session.exec(
        select(TicketDiscovery).where(
            TicketDiscovery.filed_ticket_id == ticket_id,
            col(TicketDiscovery.decision).is_(None),
        )
    ).first()


def undecided_findings_of(session: Session, source: Ticket) -> list[tuple[TicketDiscovery, Ticket]]:
    """Open findings filed against ``source`` that still wait for its scope decision."""
    rows = session.exec(
        select(TicketDiscovery, Ticket)
        .join(Ticket, col(Ticket.id) == TicketDiscovery.filed_ticket_id)
        .where(TicketDiscovery.source_ticket_id == source.id)
        .where(col(TicketDiscovery.decision).is_(None))
        .where(col(Ticket.state).not_in(StateMachine.TERMINAL_TICKET_STATES))
        .order_by(col(TicketDiscovery.filed_at))
    ).all()
    return [(row, ticket) for row, ticket in rows]


def refuse_undecided(session: Session, ticket: Ticket) -> None:
    """Refuse to start an undecided finding. A terminal one starts nothing anyway."""
    if ticket.state in StateMachine.TERMINAL_TICKET_STATES:
        return
    discovery = undecided(session, ticket.id)
    if discovery is not None:
        raise UndecidedFinding(ticket, discovery)


def record_discovery(session: Session, run: AgentRun, filed: Ticket) -> TicketDiscovery:
    """Record that ``run`` filed ``filed`` while working on its own ticket. Undecided."""
    row = TicketDiscovery(
        filed_ticket_id=filed.id,
        source_ticket_id=run.ticket_id,
        agent_run_id=run.id,
        orchestration_run_id=run.orchestration_run_id,
    )
    session.add(row)
    return row
