"""A blocked ticket can be deferred, so the tickets waiting on it proceed without it.

`dependency_hold` holds a dependent until its prerequisites land, so one blocked
prerequisite wedged every ticket behind it and stopped a milestone for a person.
fest's answer, adopted here: a deferred blocker **stays blocked**, its
dependents become ready, each told which deferred prerequisite it proceeds
without, and nothing above it can finish while it is deferred. Once other work
settles, the autopilot retries deferred tickets in sweeps (`deferred_sweep`).

- **Who:** a person, or an agent through `loregarden_defer_ticket`, gated like
  any write on a stage run.
- **What:** only a blocked ticket, and only with a reason; the reason is what
  every dependent and the parent's rollup show.
- **Ends:** when the ticket is resolved (`deferral_state.is_deferred`), or
  `undefer_ticket` takes it back.

Readiness skips a deferred prerequisite in all three places that judge it:
`dependency_readiness.unmet_prerequisites_for_start` (dispatch and release),
`TicketDependencyService.unmet_prerequisites` (no repository), and the plan
graph's `waiting_on`. A deferred ticket is still BLOCKED, so the rollup keeps
its parent blocked; `ticket_rollup` names it and why.
"""

from __future__ import annotations

from loregarden.models.domain import (
    DomainEvent,
    EventType,
    OrchestratorDecision,
    Ticket,
    TicketState,
    utcnow,
)
from loregarden.services.deferral_state import is_deferred
from loregarden.services.orchestrator_decisions import record_orchestrator_decision
from loregarden.services.ticket_dependencies import TicketDependencyService
from loregarden.services.ticket_rollup import reconcile_ancestors
from sqlmodel import Session, col, select

#: Tickets the "landed since" list names before it says how many more.
_LANDED_SHOWN = 15


class DeferralRefused(ValueError):
    """A deferral or undeferral that cannot be made, and why."""


def defer_ticket(session: Session, ticket: Ticket, reason: str, *, actor: str) -> None:
    """Defer a blocked ticket with a reason, committed."""
    reason = reason.strip()
    if not reason:
        raise DeferralRefused("a deferral needs a reason: every dependent is told it")
    if ticket.state is not TicketState.BLOCKED:
        raise DeferralRefused(
            f"{ticket.external_id} is {ticket.state.value}, not blocked; only a blocked "
            "ticket holds its dependents up"
        )
    if is_deferred(ticket):
        raise DeferralRefused(f"{ticket.external_id} is already deferred: {ticket.deferred_reason}")
    ticket.deferred_at = utcnow()
    ticket.deferred_reason = reason
    session.add(ticket)
    dependents = TicketDependencyService(session).dependents(ticket.id)
    record_orchestrator_decision(
        session,
        ticket,
        decision=OrchestratorDecision.TICKET_DEFERRED,
        stage_key=ticket.workflow_stage_key,
        reason=(
            f"{actor} deferred {ticket.external_id}: {reason}. It stays blocked; "
            f"{len(dependents)} ticket(s) waiting on it proceed without it, and nothing above "
            "it can finish until it is resolved."
        ),
        evidence={"actor": actor, "reason": reason, "dependents": dependents},
    )
    session.commit()
    reconcile_ancestors(session, ticket)


def undefer_ticket(session: Session, ticket: Ticket, *, actor: str) -> None:
    """Take a deferral back: the ticket holds its dependents again. Committed."""
    if not is_deferred(ticket):
        raise DeferralRefused(f"{ticket.external_id} is not deferred")
    reason = ticket.deferred_reason
    ticket.deferred_at = None
    ticket.deferred_reason = ""
    session.add(ticket)
    record_orchestrator_decision(
        session,
        ticket,
        decision=OrchestratorDecision.TICKET_UNDEFERRED,
        stage_key=ticket.workflow_stage_key,
        reason=f"{actor} took back the deferral of {ticket.external_id} ({reason}).",
        evidence={"actor": actor, "reason": reason},
    )
    session.commit()
    reconcile_ancestors(session, ticket)


def deferred_prerequisites(session: Session, ticket: Ticket) -> list[Ticket]:
    """The prerequisites ``ticket`` proceeds without because they are deferred."""
    ids = TicketDependencyService(session).prerequisites(ticket.id)
    if not ids:
        return []
    rows = session.exec(
        select(Ticket).where(col(Ticket.id).in_(ids)).order_by(col(Ticket.external_id))
    ).all()
    return [row for row in rows if is_deferred(row)]


def landed_since(session: Session, ticket: Ticket) -> list[Ticket]:
    """Tickets in ``ticket``'s workspace that landed after it was deferred, oldest first."""
    if ticket.deferred_at is None:
        return []
    landed_ids = session.exec(
        select(DomainEvent.ticket_id)
        .where(
            DomainEvent.type == EventType.TICKET_LANDED,
            DomainEvent.workspace_id == ticket.workspace_id,
            col(DomainEvent.created_at) > ticket.deferred_at,
            DomainEvent.ticket_id != ticket.id,
        )
        .order_by(col(DomainEvent.created_at))
    ).all()
    ordered = list(dict.fromkeys(tid for tid in landed_ids if tid))
    rows = {row.id: row for row in session.exec(select(Ticket).where(col(Ticket.id).in_(ordered)))}
    return [rows[tid] for tid in ordered if tid in rows]


def deferral_context_lines(session: Session | None, ticket: Ticket) -> list[str]:
    """The brief's deferral sections: the deferred prerequisites this ticket
    proceeds without, and, when it is itself deferred, why and what landed since."""
    if session is None:
        return []
    lines: list[str] = []
    without = deferred_prerequisites(session, ticket)
    if without:
        lines += [
            "",
            "## Deferred prerequisites you proceed without",
            "These tickets are prerequisites of this one, but are blocked and were deferred so "
            "this work could go ahead. Their work is not there; do not build on it, and say in "
            "your report if this ticket cannot be finished without it.",
            "",
        ]
        lines += [f"- {t.external_id} — {t.title}: {t.deferred_reason}" for t in without]
    if is_deferred(ticket):
        landed = landed_since(session, ticket)
        lines += [
            "",
            "## This ticket was deferred",
            f"It was deferred on {ticket.deferred_at:%Y-%m-%d %H:%M} UTC: {ticket.deferred_reason}. "
            "It is being retried because other work has settled; what it was blocked on may "
            "have changed.",
            "",
        ]
        if landed:
            shown = landed[:_LANDED_SHOWN]
            lines.append(f"Landed since it was deferred ({len(landed)}):")
            lines += [f"- {t.external_id} — {t.title}" for t in shown]
            if len(landed) > _LANDED_SHOWN:
                lines.append(f"- … and {len(landed) - _LANDED_SHOWN} more")
        else:
            lines.append("Nothing has landed in this workspace since it was deferred.")
    return lines
