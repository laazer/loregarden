"""Work a run discovered: recorded where it was filed, held out until triaged.

See `models.domain.discovery_tables` for why. This module is the one place
that answers "is this ticket part of its parent's work yet?", so every reader
that walks a parent's children (the child pass, the rollup, the terminal
sweep, the integration review, the autopilot) asks it the same way:

- `owned_children` is the parent's children minus its untriaged discoveries.
- `refuse_untriaged` is the guard at the one writer every dispatcher shares,
  `start_orchestration_run`.
- `triage` promotes a discovery into the work, or closes it.

The filing budget (`agent_ticket_filing`) counts these rows too.
"""

from __future__ import annotations

from loregarden.core.state_machine import StateMachine
from loregarden.models.domain import AgentRun, DiscoveryTriage, Ticket, TicketDiscovery, utcnow
from sqlmodel import Session, col, select


class UntriagedDiscovery(ValueError):
    """A dispatcher tried to start a discovery nobody has triaged.

    A `ValueError`, so every start path that already turns a refusal into a
    400 or an abandoned claim does the same with this one.
    """

    def __init__(self, ticket: Ticket, discovery: TicketDiscovery) -> None:
        super().__init__(
            f"{ticket.external_id} was filed by a run as a finding and has not been "
            "triaged. Promote it into the work (loregarden_triage_discovery "
            "action=promote) or close it before starting it."
        )
        self.discovery = discovery


def untriaged_ids():
    """A subquery of every untriaged discovery's ticket id."""
    return select(TicketDiscovery.filed_ticket_id).where(col(TicketDiscovery.triaged_at).is_(None))


def owned_children(parent_id: str):
    """A ``select(Ticket)`` of the children that are the parent's work.

    Every child but an untriaged discovery: that one stays where it was filed,
    and neither its parent's drive nor its parent's completion waits for it.
    """
    return select(Ticket).where(
        Ticket.parent_ticket_id == parent_id, col(Ticket.id).not_in(untriaged_ids())
    )


def has_owned_children(session: Session, parent_id: str) -> bool:
    return session.exec(owned_children(parent_id).limit(1)).first() is not None


def untriaged(session: Session, ticket_id: str) -> TicketDiscovery | None:
    """The ticket's discovery row while it is untriaged; None once triaged or never filed."""
    return session.exec(
        select(TicketDiscovery).where(
            TicketDiscovery.filed_ticket_id == ticket_id,
            col(TicketDiscovery.triaged_at).is_(None),
        )
    ).first()


def refuse_untriaged(session: Session, ticket: Ticket) -> None:
    """Refuse to start an untriaged discovery. A terminal one starts nothing anyway."""
    if ticket.state in StateMachine.TERMINAL_TICKET_STATES:
        return
    discovery = untriaged(session, ticket.id)
    if discovery is not None:
        raise UntriagedDiscovery(ticket, discovery)


def record_discovery(session: Session, run: AgentRun, filed: Ticket) -> TicketDiscovery:
    """Record that ``run`` filed ``filed`` while working on its own ticket. Untriaged."""
    row = TicketDiscovery(
        filed_ticket_id=filed.id,
        source_ticket_id=run.ticket_id,
        agent_run_id=run.id,
        orchestration_run_id=run.orchestration_run_id,
    )
    session.add(row)
    return row


def triage(
    session: Session, ticket: Ticket, action: DiscoveryTriage, *, actor: str
) -> TicketDiscovery:
    """Mark an untriaged discovery triaged. Does not commit.

    Promoting makes it its parent's work from here on. Closing is recorded
    here; the caller closes the ticket itself through the ticket service, so
    its state change goes through the same path as any other.
    """
    discovery = untriaged(session, ticket.id)
    if discovery is None:
        raise ValueError(f"{ticket.external_id} is not an untriaged discovery")
    discovery.triaged_at = utcnow()
    discovery.triaged_by = f"{actor}: {action.value}"
    session.add(discovery)
    return discovery
