"""Detector and repair: a finding whose source ticket finished undecided.

A finding is not work until its source ticket's spec decides it
(`services.discovered_work`), and `scope_round.round_due` is the only thing
that ever asks: it fires when the source enters a gate or terminal stage with
findings outstanding. A source that reaches `done` or `wont_do` without
crossing one again — closed by its parent's rollup, by an operator, or by a
sweep — can never be asked, so the decision its findings wait for is
unreachable and every dispatcher refuses them forever.

Measured on the live database on 2026-11-12: 49 of 54 undecided findings had a
source already finished, and 47 of the filed tickets were open. Four of them
were on an initiative's critical path, and its autopilot refused its first pick
two seconds after being turned on.

The repair is the decision the round would have asked for, taken by the one
party still able to take it: `defer`, which is what `scope_round` already does
for a round that decides nothing. Deferred is the honest answer, not a guess —
nobody waits on the ticket, it stays ordinary backlog work, and a person can
still promote it.
"""

from __future__ import annotations

from collections.abc import Sequence

from loregarden.core.state_machine import StateMachine
from loregarden.models.domain import FindingDecision, Ticket, TicketDiscovery
from loregarden.services.invariants import Violation
from loregarden.services.scope_round import DEFERRED_BY, decide_finding
from sqlalchemy.orm import aliased
from sqlmodel import Session, col, select

#: Said on every deferral this writes, so the row records why it was decided
#: without a spec ever seeing it.
REASON = (
    "Deferred by the control plane: this finding's source ticket finished without a scope "
    "round, so the decision it waits for can never be asked for. It is ordinary backlog "
    "work nobody waits on; a person can still promote it."
)


def _orphaned(session: Session) -> list[tuple[TicketDiscovery, Ticket, Ticket]]:
    """Every open undecided finding, with its filed ticket and its finished source."""
    source = aliased(Ticket, name="source")
    rows = session.exec(
        select(TicketDiscovery, Ticket, source)
        .join(Ticket, col(Ticket.id) == TicketDiscovery.filed_ticket_id)
        .join(source, col(source.id) == TicketDiscovery.source_ticket_id)
        .where(col(TicketDiscovery.decision).is_(None))
        .where(col(Ticket.state).not_in(StateMachine.TERMINAL_TICKET_STATES))
        .where(col(source.state).in_(StateMachine.TERMINAL_TICKET_STATES))
        .order_by(col(TicketDiscovery.filed_at))
    ).all()
    return list(rows)


def findings_whose_source_finished(session: Session) -> list[Violation]:
    """Open undecided findings whose source can no longer be asked to decide them.

    `entered_at` is the source's `resolved_at`: the moment the decision became
    unreachable. A source resolved before that column was stamped reads as
    unknown, which the reconcile pass treats as backlog rather than re-entry.
    """
    return [
        Violation(
            subject_id=filed.id,
            ticket_id=filed.id,
            entered_at=src.resolved_at,
            detail=(
                f"{filed.external_id} waits for a scope decision from "
                f"{src.external_id}, which is {src.state.value}"
            ),
        )
        for _discovery, filed, src in _orphaned(session)
    ]


def defer_findings_whose_source_finished(
    session: Session,
    violations: Sequence[Violation],  # noqa: ARG001 - re-read: the world may have moved
) -> int:
    """Defer every such finding. Re-reads the state itself; `decide_finding` commits."""
    repaired = 0
    for _discovery, filed, _src in _orphaned(session):
        decide_finding(session, filed, FindingDecision.DEFER, reason=REASON, decided_by=DEFERRED_BY)
        repaired += 1
    return repaired
