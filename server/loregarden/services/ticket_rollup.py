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

And it will not close a parent whose own branch carries work that landed
nowhere (`parent_own_work`): it holds it blocked for a person instead.

An initiative is the one parent whose "children" are not only its children: it
summarizes every top-level ticket it covers, members included
(`initiative_coverage`). So a member's change also reconciles each initiative
tracking it, though no `parent_ticket_id` leads there.
"""

from __future__ import annotations

import logging

from loregarden.core.event_bus import event_bus
from loregarden.models.domain import BlockKind, EventType, Ticket, TicketState, WorkItemType
from loregarden.services.deferral_state import is_deferred
from loregarden.services.discovered_work import has_owned_children, owned_children
from loregarden.services.initiative_coverage import (
    initiative_roots,
    initiatives_tracking,
    member_initiative_ids,
)
from loregarden.services.parent_own_work import unlanded_own_work
from loregarden.services.ticket_state_service import RESOLVED_STATES, derive
from sqlmodel import Session, col, select

logger = logging.getLogger(__name__)

# RESOLVED_STATES (re-exported): a child in one of these has nothing left to
# contribute to its parent.


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
    if all(state in RESOLVED_STATES for state in child_states):
        return TicketState.DONE
    if any(state != TicketState.BACKLOG for state in child_states):
        return TicketState.IN_PROGRESS
    return TicketState.BACKLOG


def has_children(session: Session, ticket_id: str) -> bool:
    """Whether this ticket is a parent, and so does not own its own state.

    The predicate the workflow side asks before deriving a state from stages: a
    parent's stages are never run, so deriving from them answers `backlog`
    forever and overwrites whatever the rollup just worked out. An undecided or deferred
    finding filed under it does not make it one (`discovered_work`).
    """
    return has_owned_children(session, ticket_id)


def _child_states(session: Session, parent_id: str) -> list[TicketState]:
    return [child.state for child in _children(session, parent_id)]


def _children(session: Session, parent_id: str) -> list[Ticket]:
    """What a parent summarizes: its owned children, and an initiative's members too."""
    parent = session.get(Ticket, parent_id)
    if parent is not None and parent.work_item_type == WorkItemType.INITIATIVE:
        # Its children and its members (`initiative_coverage`).
        return list(initiative_roots(session, [parent_id])[parent_id].roots)
    return list(session.exec(owned_children(parent_id)).all())


def reconcile_parent(session: Session, parent: Ticket) -> bool:
    """Align one parent with its children. True when it changed.

    Does not commit — callers batch this with whatever else they are writing.
    """
    target = derive_parent_state(_child_states(session, parent.id))
    if target is None:
        return False
    held = _held_open(session, parent, target)
    if held:
        target = TicketState.BLOCKED
    # `derive` owns the state_locked / wont_do guards and the bookkeeping; a
    # parent's state is a function of its children, not a move anyone chose.
    moved = derive(parent, target, actor="rollup")
    noted = _note_own_work(parent, held)
    stamped = _stamp_tickets_kind(parent, target)
    return _note_deferred_children(session, parent) or noted or stamped or moved


#: How a rollup note holding a parent open over its own unlanded work starts,
#: so the rollup only ever clears a note it wrote.
OWN_WORK_NOTE = "Not closed: "


def _held_open(session: Session, parent: Ticket, target: TicketState) -> str:
    """Why a parent the rollup would close must stay open, or "".

    Asked only on the way to `done` (git is the only witness, and this is the
    rare moment), and not of a ticket `derive` would refuse anyway.
    """
    if target is not TicketState.DONE or parent.state is TicketState.DONE:
        return ""
    if parent.state_locked or parent.state in (TicketState.WONT_DO, TicketState.PARKED):
        return ""
    return unlanded_own_work(session, parent)


def _note_own_work(parent: Ticket, held: str) -> bool:
    """Say why a parent whose children are resolved is held open. True when it changed.

    Blocked for a person (`human_action`): only landing its branch or closing it
    by hand clears it, and the next pass after a landing closes it. A block its
    own run recorded keeps that run's kind and words.
    """
    ours = parent.block_kind in (None, BlockKind.TICKETS) or parent.blocking_issues.startswith(
        OWN_WORK_NOTE
    )
    if held:
        if not ours:
            return False
        note = f"{OWN_WORK_NOTE}{held}"
        if parent.block_kind is BlockKind.HUMAN_ACTION and parent.blocking_issues == note:
            return False
        parent.block_kind = BlockKind.HUMAN_ACTION
        parent.blocking_issues = note
        return True
    if not parent.blocking_issues.startswith(OWN_WORK_NOTE):
        return False
    parent.blocking_issues = ""
    if parent.block_kind is BlockKind.HUMAN_ACTION:
        parent.block_kind = None
    return True


#: How a rollup note naming deferred children starts, so the rollup only ever
#: clears a note it wrote.
DEFERRED_NOTE = "Waits on deferred "
#: How a rollup note naming the blocked tickets under a parent starts.
BLOCKED_UNDER_NOTE = "Blocked under it: "
#: Tickets a note names before the rest become a count.
_DEFERRED_NAMED = 3
#: How much of a blocked descendant's own reason the note repeats.
_BLOCKER_WORDS = 90


def _note_deferred_children(session: Session, parent: Ticket) -> bool:
    """Say what keeps a rolled-up parent blocked: deferred children, else the
    blocked tickets under it. True when it changed.

    A deferred child stays blocked, so the parent is blocked by the rollup
    (`ticket_deferral`); without this its card read "blocked" with no hint
    that the block was a decision someone made. A parent blocked by any other
    descendant read "blocked" and nothing: an initiative over 493 over 1420
    over 1431, and the operator asked on four dates which ticket it meant.
    """
    note = _rolled_up_note(session, parent) if parent.block_kind is BlockKind.TICKETS else ""
    ours = parent.blocking_issues.startswith((DEFERRED_NOTE, BLOCKED_UNDER_NOTE))
    if not note and not ours:
        return False
    if note and parent.blocking_issues and not ours:
        return False  # words the parent's own run wrote are not the rollup's to replace
    if parent.blocking_issues == note:
        return False
    parent.blocking_issues = note
    return True


def _rolled_up_note(session: Session, parent: Ticket) -> str:
    deferred = [child for child in _children(session, parent.id) if is_deferred(child)]
    if deferred:
        return DEFERRED_NOTE + _named(
            [f"{child.external_id} ({child.deferred_reason})" for child in deferred]
        )
    blockers = _blocked_leaves(session, parent)
    if not blockers:
        return ""
    return BLOCKED_UNDER_NOTE + _named([_blocker(leaf) for leaf in blockers])


def _named(items: list[str]) -> str:
    named = "; ".join(items[:_DEFERRED_NAMED])
    rest = len(items) - _DEFERRED_NAMED
    return named + (f"; and {rest} more" if rest > 0 else "")


def _blocked_leaves(session: Session, parent: Ticket) -> list[Ticket]:
    """The blocked tickets under ``parent`` that are blocked for a reason of their
    own, not because something under them is. A cycle stops the walk."""
    found: list[Ticket] = []
    seen = {parent.id}
    frontier = [parent]
    while frontier:
        node = frontier.pop(0)
        for child in _children(session, node.id):
            if child.id in seen or child.state is not TicketState.BLOCKED:
                continue
            seen.add(child.id)
            if child.block_kind is BlockKind.TICKETS:
                frontier.append(child)
            else:
                found.append(child)
    return found


def _blocker(ticket: Ticket) -> str:
    kind = ticket.block_kind.value if ticket.block_kind is not None else "unclassified"
    words = " ".join(ticket.blocking_issues.split())
    if len(words) > _BLOCKER_WORDS:
        words = words[: _BLOCKER_WORDS - 1].rstrip() + "…"
    return f"{ticket.external_id} ({kind}: {words})" if words else f"{ticket.external_id} ({kind})"


def _stamp_tickets_kind(parent: Ticket, target: TicketState) -> bool:
    """Say *why* a rolled-up parent is blocked: its children. True when it changed.

    Without it a parent blocked by a child carried no kind, and read exactly
    like a ticket a person had to look at. Only a kind the rollup owns is ever
    replaced: a parent whose own run recorded a block keeps that kind.
    """
    rolled_up = parent.state == TicketState.BLOCKED and target == TicketState.BLOCKED
    if rolled_up and parent.block_kind is None:
        parent.block_kind = BlockKind.TICKETS
        return True
    if not rolled_up and parent.block_kind is BlockKind.TICKETS:
        parent.block_kind = None
        return True
    return False


def _announce_rollup(session: Session, changed: list[Ticket]) -> None:
    """One TicketStateChanged per parent the walk just committed.

    Called only after that commit. ``publish`` commits on its own, so doing it
    inside the ancestor walk would persist a partial chain. The boot sweep
    never enters ``reconcile_ancestors``, so it stays silent.
    """
    for parent in changed:
        event_bus.publish(
            session,
            EventType.TICKET_STATE_CHANGED,
            workspace_id=parent.workspace_id,
            ticket_id=parent.id,
            payload={"state": parent.state.value, "actor": "rollup"},
        )


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
    changed += _reconcile_tracking(session, [ticket.id, *(t.id for t in changed)], seen)
    if changed:
        session.commit()
        _announce_rollup(session, changed)
    return changed


def _reconcile_tracking(session: Session, moved: list[str], seen: set[str]) -> list[Ticket]:
    """Reconcile each initiative that tracks one of ``moved`` as a member.

    An initiative has no parent, so nothing above it can change in turn.
    """
    changed: list[Ticket] = []
    for initiative_id in initiatives_tracking(session, moved):
        if initiative_id in seen:
            continue
        seen.add(initiative_id)
        initiative = session.get(Ticket, initiative_id)
        if initiative is not None and reconcile_parent(session, initiative):
            changed.append(initiative)
    return changed


def reconcile_lineage(session: Session, parent_id: str | None) -> list[Ticket]:
    """Reconcile a parent whose *set* of children changed, then everything above it.

    `reconcile_ancestors` starts from a child whose state moved. A reparent or a
    delete moves no child's state — it adds or removes a child — so the parent
    that gained or lost one is the first ticket to re-derive, not its parent.
    Without this, attaching a finished milestone to an initiative left the
    initiative showing its old state until the next startup sweep.
    """
    if not parent_id:
        return []
    parent = session.get(Ticket, parent_id)
    if parent is None:
        return []
    if not reconcile_parent(session, parent):
        return []
    session.add(parent)
    session.commit()
    return [parent, *reconcile_ancestors(session, parent)]


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
    } | member_initiative_ids(session)
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
