"""A parent's state follows its children.

A ticket with children carries no work of its own — the children do, and the
orchestrator refuses to run a parent's own stages for exactly that reason. Its
state is therefore a *summary*, and nothing kept the summary honest: the only
thing that ever moved a parent was orchestrating the parent itself
(`subtree_auto_run.finalize_aggregator_ticket`, reachable only from inside the
parent's own run). Now that the queue runs one ticket per lane, children
routinely finish without their parent ever running, and the board keeps showing
a feature in progress whose every child is done.

Two halves, because neither is sufficient alone:

- **Push upward** when a child changes, so the board is right immediately.
- **A sweep on startup**, because the push is one more write path a caller has
  to remember, and this repository has just spent four commits on the failure
  mode where one forgot. The sweep costs a query per parent at boot and makes
  a missed hook a delay rather than a permanent lie.

What this will not touch:

- `state_locked` tickets. That flag is how an operator says "I decided this";
  `update_ticket_manual` sets it whenever a human names a state, and
  `mark_aggregator_done` sets it when the orchestrator finalises a parent. A
  rollup that ignored it would silently undo both.
- `wont_do`. Abandoning a parent is a statement about the parent, not a tally
  of its children.
- Tickets with no children. A leaf's state comes from its own workflow; this
  module has nothing to say about it.
"""

from __future__ import annotations

import logging

from loregarden.models.domain import Ticket, TicketState
from loregarden.services.ticket_state_service import derive
from sqlmodel import Session, col, select

logger = logging.getLogger(__name__)

#: A child in one of these has nothing left to contribute to its parent.
_RESOLVED = (TicketState.DONE, TicketState.WONT_DO)


def derive_parent_state(child_states: list[TicketState]) -> TicketState | None:
    """What a parent's state should be, given its children. None if it has none.

    Ordered the same way `workflow_state._derive_ticket_state` orders stages,
    and for the same reason: blocked is the most urgent thing true of a tree, so
    it wins over "mostly finished". A parent whose children are all resolved is
    done even if some were skipped — `wont_do` is a resolution, not a gap.
    """
    if not child_states:
        return None
    if any(state == TicketState.BLOCKED for state in child_states):
        return TicketState.BLOCKED
    if all(state in _RESOLVED for state in child_states):
        return TicketState.DONE
    if any(state != TicketState.BACKLOG for state in child_states):
        return TicketState.IN_PROGRESS
    return TicketState.BACKLOG


def has_children(session: Session, ticket_id: str) -> bool:
    """Whether this ticket is a parent, and so does not own its own state.

    The predicate the workflow side asks before deriving a state from stages: a
    parent's stages are never run, so deriving from them answers `backlog`
    forever and overwrites whatever the rollup just worked out.
    """
    return (
        session.exec(select(Ticket.id).where(Ticket.parent_ticket_id == ticket_id).limit(1)).first()
        is not None
    )


def _child_states(session: Session, parent_id: str) -> list[TicketState]:
    return list(
        session.exec(select(Ticket.state).where(Ticket.parent_ticket_id == parent_id)).all()
    )


def reconcile_parent(session: Session, parent: Ticket) -> bool:
    """Align one parent with its children. True when it changed.

    Does not commit — callers batch this with whatever else they are writing.
    """
    target = derive_parent_state(_child_states(session, parent.id))
    if target is None:
        return False
    # `derive` owns the state_locked / wont_do guards and the bookkeeping; a
    # parent's state is a function of its children, not a move anyone chose.
    return derive(parent, target, actor="rollup")


def reconcile_ancestors(session: Session, ticket: Ticket) -> list[Ticket]:
    """Walk up from a changed ticket, reconciling every parent above it.

    The whole chain, not just the immediate parent: a task finishing can complete
    its capability, which completes its feature, which completes its milestone.
    Stops at the first ancestor that does not move, since nothing above it can
    have changed either — and guards against a parent cycle, which the schema
    permits even though the hierarchy rules do not.
    """
    changed: list[Ticket] = []
    seen: set[str] = {ticket.id}
    parent_id = ticket.parent_ticket_id
    while parent_id and parent_id not in seen:
        seen.add(parent_id)
        parent = session.get(Ticket, parent_id)
        if not parent:
            break
        if not reconcile_parent(session, parent):
            break
        changed.append(parent)
        parent_id = parent.parent_ticket_id
    if changed:
        session.commit()
    return changed


def reconcile_all_parents(session: Session) -> list[Ticket]:
    """Sweep every parent whose state disagrees with its children.

    Runs at startup. Deepest first, so a capability corrected on this pass is
    already right when its feature is judged and the whole tree settles in one
    go rather than one level per boot.
    """
    parent_ids = {
        row
        for row in session.exec(
            select(Ticket.parent_ticket_id).where(col(Ticket.parent_ticket_id).is_not(None))
        ).all()
        if row
    }
    if not parent_ids:
        return []

    parents = list(session.exec(select(Ticket).where(col(Ticket.id).in_(parent_ids))).all())
    by_id = {parent.id: parent for parent in parents}

    def depth(parent: Ticket) -> int:
        steps = 0
        seen: set[str] = {parent.id}
        current = parent.parent_ticket_id
        while current and current not in seen:
            seen.add(current)
            steps += 1
            node = by_id.get(current)
            current = node.parent_ticket_id if node else None
        return steps

    changed = [
        parent
        for parent in sorted(parents, key=depth, reverse=True)
        if reconcile_parent(session, parent)
    ]
    if changed:
        session.commit()
        logger.info(
            "Rollup sweep corrected %d parent ticket(s): %s",
            len(changed),
            ", ".join(t.external_id or t.id for t in changed),
        )
    return changed
