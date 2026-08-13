"""The one place `tickets.state` is written.

Eight call sites used to assign it directly, each remembering its own
bookkeeping — the revision bump, who last touched it, whether to emit
`TicketStateChanged` — and one of them, `update_ticket_manual`, took any value
from the API and wrote it unchecked. `StateMachine.TICKET_TRANSITIONS` existed
the whole time and was consulted at exactly one of the eight.

**Chosen versus derived.** Enforcing that table everywhere was the obvious plan
and it is wrong, because the table rejects things the system correctly does:
the parent rollup reopens a `done` parent to `in_progress`, `block_ticket`
blocks a ticket dispatched straight from the backlog, and
`reconcile_workflow_state` can settle a `blocked` ticket to `done`. None of
those are decisions anybody made — they are recomputations of a state that is a
function of something else (the stage map, the children). A transition table
describes *moves*, and a recomputation is not a move.

So there are two doors:

- `choose` — an operator, an agent or the orchestrator deciding a new state.
  Checked against the table.
- `derive` — the state recomputed from stages or children. Not checked, because
  there is nothing to check it against; the inputs already decided.

Both share the bookkeeping, which is the actual point of this module.

**What the table now permits** is wider than before, and deliberately: two
entries were missing rather than protective. See `_CHOSEN_TRANSITIONS`.
"""

from __future__ import annotations

import logging

from loregarden.core.event_bus import event_bus
from loregarden.models.domain import EventType, Ticket, TicketState
from sqlmodel import Session

logger = logging.getLogger(__name__)

#: Moves an operator, agent or orchestrator may choose.
#:
#: Two additions over `StateMachine.TICKET_TRANSITIONS`, both for transitions
#: the system already performed and the table rejected:
#:
#: - ``backlog -> blocked``: a ticket can be dispatched straight from the
#:   backlog, and the run that fails on it blocks it. The old table required it
#:   to pass through ``in_progress`` first, which is only true by accident of
#:   `start_ticket` happening to run.
#: - ``blocked -> done``: a blocked ticket whose problem turns out to be moot is
#:   finished, not first unblocked and then finished in two writes.
#: - ``backlog -> done``: closing something nobody ever started. The board and
#:   the API have always allowed it and operators use it constantly.
#:
#: What is left is thin, and worth saying plainly: between the three *open*
#: states anything goes, so the only rule this table still encodes is that a
#: ticket already finished or abandoned reopens through `backlog` rather than
#: jumping straight back into flight. Reopening as a *consequence* — a child
#: reviving its parent — is `derive`, which does not consult this at all.
_CHOSEN_TRANSITIONS: dict[TicketState, frozenset[TicketState]] = {
    TicketState.BACKLOG: frozenset(
        {TicketState.IN_PROGRESS, TicketState.BLOCKED, TicketState.DONE, TicketState.WONT_DO}
    ),
    TicketState.IN_PROGRESS: frozenset(
        {TicketState.BLOCKED, TicketState.DONE, TicketState.BACKLOG, TicketState.WONT_DO}
    ),
    TicketState.BLOCKED: frozenset(
        {TicketState.IN_PROGRESS, TicketState.BACKLOG, TicketState.DONE, TicketState.WONT_DO}
    ),
    TicketState.DONE: frozenset({TicketState.BACKLOG, TicketState.WONT_DO}),
    TicketState.WONT_DO: frozenset({TicketState.BACKLOG, TicketState.IN_PROGRESS}),
}


class InvalidTicketTransition(ValueError):
    """A chosen move the state machine does not allow."""


def can_choose(current: TicketState, target: TicketState) -> bool:
    return target in _CHOSEN_TRANSITIONS.get(current, frozenset())


def _write(
    session: Session | None,
    ticket: Ticket,
    target: TicketState,
    *,
    actor: str,
    emit: bool,
) -> bool:
    """The bookkeeping every state write owes, in one place. False if unchanged.

    `session` may be None for callers that deliberately mutate in memory and let
    whoever owns the transaction commit — `reconcile_workflow_state` is written
    that way on purpose, and threading a session through it would mean adding
    one to `set_stage_status` and its ten call sites to no benefit. Without a
    session there is nothing to publish an event on, so `emit` is ignored.
    """
    if ticket.state == target:
        return False

    previous = ticket.state
    ticket.state = target
    ticket.revision += 1
    ticket.last_updated_by = actor
    if session is not None:
        session.add(ticket)

    logger.info(
        "Ticket %s: %s -> %s (%s)",
        ticket.external_id or ticket.id,
        previous.value,
        target.value,
        actor,
    )
    if emit and session is not None:
        event_bus.publish(
            session,
            EventType.TICKET_STATE_CHANGED,
            workspace_id=ticket.workspace_id,
            ticket_id=ticket.id,
            payload={"state": target.value, "previous": previous.value, "actor": actor},
        )
    return True


def choose(
    session: Session | None,
    ticket: Ticket,
    target: TicketState,
    *,
    actor: str,
    emit: bool = True,
) -> bool:
    """Move a ticket because someone decided to. Validated. False if unchanged.

    Raises `InvalidTicketTransition` rather than writing a state the machine
    does not allow from here.
    """
    if ticket.state == target:
        return False
    if not can_choose(ticket.state, target):
        raise InvalidTicketTransition(
            f"Invalid ticket transition {ticket.state.value} -> {target.value}"
        )
    return _write(session, ticket, target, actor=actor, emit=emit)


def derive(
    session: Session | None,
    ticket: Ticket,
    target: TicketState,
    *,
    actor: str,
    emit: bool = False,
) -> bool:
    """Recompute a ticket's state from its stages or children. False if unchanged.

    Unvalidated on purpose — see the module docstring. Refuses two things the
    inputs have no authority over:

    - a `state_locked` ticket, which is how an operator says "I decided this"
    - a `wont_do` ticket, since abandoning is a statement about the ticket
      rather than a tally of its parts
    """
    if ticket.state_locked or ticket.state == TicketState.WONT_DO:
        return False
    return _write(session, ticket, target, actor=actor, emit=emit)
