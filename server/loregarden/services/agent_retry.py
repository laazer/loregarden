"""Whether a block an agent could clear still can, and who starts the re-run.

`BlockKind` says a harness or work block is an agent's to clear, but the kind
outlives the chance: once the stage's repair turn is spent, or its retry budget
is, the block waits for a person. Reading the kind alone tagged those "Agent can
clear" for weeks. `autopilot_retry.retry_reason` is the one answer to whether a
re-run could still clear it; this module asks it, and then asks whether
anything will start that re-run without a person (the initiative autopilot,
which retries a blocked ticket of its own once per time it is turned on).
"""

from __future__ import annotations

from loregarden.models.domain import AgentRetry, BlockedOn, InitiativePlan, Ticket, TicketState
from loregarden.services.autopilot_retry import retried_since, retry_reason
from loregarden.services.block_repair import REPAIRABLE_KINDS
from loregarden.services.initiative_coverage import covered_ticket_ids
from sqlmodel import Session, col, select


def _agent_block(ticket: Ticket) -> bool:
    return ticket.state == TicketState.BLOCKED and ticket.block_kind in REPAIRABLE_KINDS


def can_retry(session: Session, ticket: Ticket) -> bool:
    """A harness or work block a re-run could still clear."""
    return _agent_block(ticket) and retry_reason(session, ticket) is not None


def _autopilot_will_retry(session: Session) -> set[str]:
    """Tickets an autopilot that is on covers and has not yet retried since it was turned on."""
    plans = session.exec(
        select(InitiativePlan).where(col(InitiativePlan.autopilot).is_(True))
    ).all()
    ids: set[str] = set()
    for plan in plans:
        retried = retried_since(session, plan.initiative_id, plan.autopilot_since)
        ids.update(set(covered_ticket_ids(session, plan.initiative_id)) - retried)
    return ids


def agent_retry_map(session: Session, tickets: list[Ticket]) -> dict[str, AgentRetry]:
    """Who starts the re-run, for each ticket a re-run could still clear; absent otherwise."""
    retryable = [t for t in tickets if can_retry(session, t)]
    if not retryable:
        return {}
    automatic = _autopilot_will_retry(session)
    return {
        t.id: AgentRetry.AUTOMATIC if t.id in automatic else AgentRetry.ON_REQUEST
        for t in retryable
    }


def agent_retry_for(
    answers: dict[str, BlockedOn], retry: dict[str, AgentRetry]
) -> dict[str, AgentRetry]:
    """`retry` kept only where `blocked_on` answered AGENT: a retryable block that
    also waits on prerequisites is held at its dispatch, so no re-run comes next."""
    return {tid: how for tid, how in retry.items() if answers.get(tid) is BlockedOn.AGENT}


def spent_agent_blocks(session: Session) -> list[str]:
    """Every harness or work block with no re-run left: a person's, whatever its kind says.

    For the SQL filter, which cannot ask `retry_reason`. Blocked tickets of
    these kinds number in the tens, so this is bounded by them, not the board.
    """
    blocked = session.exec(
        select(Ticket).where(
            Ticket.state == TicketState.BLOCKED,
            col(Ticket.block_kind).in_(REPAIRABLE_KINDS),
        )
    ).all()
    return [t.id for t in blocked if retry_reason(session, t) is None]
