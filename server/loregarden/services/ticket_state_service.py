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
from loregarden.models.domain import EventType, Ticket, TicketState, utcnow
from sqlalchemy.orm import object_session
from sqlmodel import Session

logger = logging.getLogger(__name__)

#: States meaning the work is behind us — finished or abandoned. Entering one
#: stamps `resolved_at`; leaving one clears it.
RESOLVED_STATES = (TicketState.DONE, TicketState.WONT_DO)

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
#: PARKED joins the open states, so anything goes between it and the other
#: three. It is reachable from BLOCKED in particular because that is the move
#: this exists for: an agent reported `blocked` on human-only work, and a person
#: (or the parking call) reclassifies it as owed rather than stuck.
_CHOSEN_TRANSITIONS: dict[TicketState, frozenset[TicketState]] = {
    TicketState.BACKLOG: frozenset(
        {
            TicketState.IN_PROGRESS,
            TicketState.BLOCKED,
            TicketState.PARKED,
            TicketState.DONE,
            TicketState.WONT_DO,
        }
    ),
    TicketState.IN_PROGRESS: frozenset(
        {
            TicketState.BLOCKED,
            TicketState.PARKED,
            TicketState.DONE,
            TicketState.BACKLOG,
            TicketState.WONT_DO,
        }
    ),
    TicketState.BLOCKED: frozenset(
        {
            TicketState.IN_PROGRESS,
            TicketState.PARKED,
            TicketState.BACKLOG,
            TicketState.DONE,
            TicketState.WONT_DO,
        }
    ),
    TicketState.PARKED: frozenset(
        {
            TicketState.IN_PROGRESS,
            TicketState.BLOCKED,
            TicketState.BACKLOG,
            TicketState.DONE,
            TicketState.WONT_DO,
        }
    ),
    TicketState.DONE: frozenset({TicketState.BACKLOG, TicketState.WONT_DO}),
    TicketState.WONT_DO: frozenset({TicketState.BACKLOG, TicketState.IN_PROGRESS}),
}


class InvalidTicketTransition(ValueError):
    """A chosen move the state machine does not allow."""


def can_choose(current: TicketState, target: TicketState) -> bool:
    return target in _CHOSEN_TRANSITIONS.get(current, frozenset())


def _write(
    ticket: Ticket,
    target: TicketState,
    *,
    actor: str,
) -> bool:
    """The bookkeeping every state write owes, in one place. False if unchanged.

    Mutates only. Persistence is the caller's transaction to commit: a ticket
    reached through a session is already tracked, so SQLAlchemy writes a dirty
    field at commit whether or not anything called `add` on it.
    """
    if ticket.state == target:
        return False

    previous = ticket.state
    ticket.state = target
    ticket.revision += 1
    ticket.last_updated_by = actor
    if target not in RESOLVED_STATES:
        ticket.resolved_at = None
    elif previous not in RESOLVED_STATES:
        # done -> wont_do keeps the original stamp: the work left the plan then.
        ticket.resolved_at = utcnow()

    logger.info(
        "Ticket %s: %s -> %s (%s)",
        ticket.external_id or ticket.id,
        previous.value,
        target.value,
        actor,
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
    does not allow from here. A decision is worth announcing, so this one takes
    a session in order to publish `TicketStateChanged`; pass ``emit=False`` when
    the caller publishes its own, richer event.
    """
    if ticket.state == target:
        return False
    if not can_choose(ticket.state, target):
        raise InvalidTicketTransition(
            f"Invalid ticket transition {ticket.state.value} -> {target.value}"
        )
    if not _write(ticket, target, actor=actor):
        return False
    if emit and session is not None:
        event_bus.publish(
            session,
            EventType.TICKET_STATE_CHANGED,
            workspace_id=ticket.workspace_id,
            ticket_id=ticket.id,
            payload={"state": target.value, "actor": actor},
        )
    return True


def record_derived_change(ticket: Ticket, *, actor: str) -> None:
    """Log `TicketStateChanged` for a state `derive` just wrote, in the same transaction.

    `derive` writes no event, so every close through the stage map, which is
    nearly every close, left the event log silent: no ticket's history ever
    showed it reaching `done`. `event_bus.publish` commits, which is why
    `derive` could not call it, so `event_bus.add` puts the row in the session
    that holds the ticket instead, and it commits with the state or not at all.
    """
    session = object_session(ticket)
    if session is None:
        # The write this would describe is not in any transaction either, so it
        # persists only if a caller adds the ticket later, and that caller is
        # what has to be found.
        logger.warning(
            "Ticket %s: derived state %s is not in a session; its TicketStateChanged was not logged",
            ticket.external_id or ticket.id,
            ticket.state.value,
        )
        return
    event_bus.add(
        session,
        EventType.TICKET_STATE_CHANGED,
        workspace_id=ticket.workspace_id,
        ticket_id=ticket.id,
        payload={"state": ticket.state.value, "actor": actor},
    )


def derive(ticket: Ticket, target: TicketState, *, actor: str) -> bool:
    """Recompute a ticket's state from its stages or children. False if unchanged.

    No session, because it needs none: the ticket it is handed is already
    tracked by whichever transaction will commit it. It logs no event itself,
    because its two callers announce differently: the stage map through
    `record_derived_change`, in its transaction, and the parent rollup after its
    commit (`ticket_rollup._announce_rollup`). Unvalidated on purpose — see the module docstring. Refuses two
    things the inputs have no authority over:

    - a `state_locked` ticket, which is how an operator says "I decided this"
    - a `wont_do` ticket, since abandoning is a statement about the ticket
      rather than a tally of its parts
    - a `parked` ticket, for the same reason: parking says a person owes this
      work, which no recomputation over stages or children can know. Parking
      through `update_ticket_manual` also sets `state_locked`, so this guard is
      belt and braces — but it is the honest place for the rule, and it keeps a
      parked ticket parked when it is reached by any other writer
      (lg-workflow-integrity-449).
    """
    if ticket.state_locked or ticket.state in (TicketState.WONT_DO, TicketState.PARKED):
        return False
    return _write(ticket, target, actor=actor)
