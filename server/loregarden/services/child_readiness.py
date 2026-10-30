"""May any ticket in this subtree take a step?

Two callers need the same answer, and they used to read it at different times.

`BuiltinOrchestrator` reads it *inside* a run: `partition_children` splits one
parent's children into those that may run now and the two kinds of hold, and
the driver pauses when nothing may.

The initiative autopilot needs it *before* it spends a slot. Its pre-dispatch
check was `unmet_prerequisites_for_start` on the picked ticket alone, which a
parent passes whenever its own prerequisites are met — and a parent carries no
stages of its own, so a parent whose every child is held gets queued, cuts a
lane, and the orchestrator discovers one layer later that there is nothing to
run. That pause is stamped SUCCEEDED, so the same pick wins the next tick and
the loop never ends. The knowledge was always here; it was read too late.

So the partition lives here rather than in the driver, and `subtree_start_hold`
walks the subtree with it: a parent is startable when *something* under it is,
or when every child is already complete (the parent then finalizes).
"""

from __future__ import annotations

from loregarden.models.domain import (
    WORKFLOW_WORK_ITEM_TYPES,
    StageStatus,
    Ticket,
    TicketState,
    Workspace,
)
from loregarden.services.dependency_readiness import (
    UnmetPrerequisite,
    UnmetReason,
    unmet_prerequisites_for_start,
)
from loregarden.services.hold_line import HeldChild, render_hold_line
from loregarden.services.subtree_auto_run import order_children_for_subtree
from loregarden.services.ticket_dependencies import TicketDependencyService
from loregarden.services.workflow_service import resolve_ticket_stages, workflow_instance_for
from loregarden.services.workflow_state import parse_stage_map
from sqlmodel import Session, select


def ordered_children(session: Session, ticket: Ticket) -> list[Ticket]:
    """``ticket``'s direct children, in the order a subtree pass would run them."""
    children = list(session.exec(select(Ticket).where(Ticket.parent_ticket_id == ticket.id)).all())
    prereqs = TicketDependencyService(session).prerequisites_map([c.id for c in children])
    return order_children_for_subtree(children, prereqs)


def partition_children(
    session: Session, workspace: Workspace | None, children: list[Ticket]
) -> tuple[list[Ticket], list[str], list[HeldChild]]:
    """Split ordered children into those that may run now, and two kinds of hold.

    Deciding *whether* a child may run is a different job from running it, and
    separating them keeps the run loop to the one thing it does. Both holds leave
    the parent incomplete; they differ in who is owed:

    - ``parked`` is owed by a person, and deliberately does not stop the rest of
      the subtree (lg-workflow-integrity-449);
    - ``waiting`` is owed by another ticket, whose work this run cannot reach.

    That last qualifier is narrower than it looks, and reading it too widely is
    what produced lg-milestone-that-779. A sibling exemption is right for one
    reason only: `order_children_for_subtree` has already put a sibling that
    has *not run* earlier in this list, so blocking on it would hold a child
    whose prerequisite is about to run — an integration review depends on every
    sibling, so it would never start at all. That is `UnmetReason.NOT_DONE`,
    and it stays exempt.

    Ordering cannot do the other half. A sibling in `done` whose branch never
    landed has already had its turn, and no reordering makes work the
    dependent's base cannot see appear in it (`UnmetReason.NOT_LANDED`). 779
    observed exactly that: 715 reached `done` unlanded, 716 was dispatched
    immediately, and 716's worktree was cut from an integration branch holding
    none of 715's commits. So the exemption is keyed on the *reason*, per
    entry, and never on being a sibling alone.

    What ordering cannot express is a prerequisite it never saw. It drops edges
    pointing outside the set, which is every cross-workspace edge and every edge
    to a ticket under a different parent, so before this the orchestrator picked
    up children whose prerequisite had not been started — in another repository —
    and reported the failure as the child's own (676). Those are exactly the
    edges nothing in this run can satisfy, which is what makes them a hold
    rather than an ordering hint.
    """
    sibling_ids = {child.id for child in children}
    runnable: list[Ticket] = []
    parked: list[str] = []
    waiting: list[HeldChild] = []
    for child in children:
        if child.work_item_type not in WORKFLOW_WORK_ITEM_TYPES:
            continue
        if child.state == TicketState.PARKED:
            parked.append(child.title)
            continue
        unmet = [
            entry
            for entry in unmet_for(session, child, workspace)
            if not (entry.ticket.id in sibling_ids and entry.reason is UnmetReason.NOT_DONE)
        ]
        if unmet:
            waiting.append(HeldChild(ticket=child, unmet=unmet))
            continue
        runnable.append(child)
    return runnable, parked, waiting


def unmet_for(
    session: Session, child: Ticket, workspace: Workspace | None
) -> list[UnmetPrerequisite]:
    """Landed-readiness when there is a repository to ask; state alone otherwise."""
    if workspace is not None:
        return unmet_prerequisites_for_start(session, child, workspace)
    return [
        UnmetPrerequisite(prereq, UnmetReason.NOT_DONE)
        for prereq in TicketDependencyService(session).unmet_prerequisites(child.id)
    ]


def subtree_start_hold(session: Session, ticket: Ticket, workspace: Workspace | None) -> str:
    """Why nothing under ``ticket`` can take a step, or "" when something can.

    Read before a slot is spent, so it answers for the whole subtree the way a
    run would walk it, and never writes: `has_work_left` reads the stage map a
    run would set up rather than calling `ensure_workflow_instance`.

    A leaf is always startable — its own prerequisites are the caller's check,
    not this one's. A parent is startable when any incomplete descendant is, and
    when every child is complete (the run then finalizes the parent, which is a
    step). Otherwise the reason is the deepest one that applies, since "1420 is
    waiting" is useless to an operator who needs to know that 1431 wants 1661.
    """
    return _hold(session, ticket, workspace, seen=set())


def has_work_left(session: Session, ticket: Ticket) -> bool:
    """Whether ``ticket`` still has a stage to run, read without writing one.

    `ticket_workflow_complete` is the driver's predicate, and it answers for a
    ticket whose instance the driver has just ensured: there, no instance means
    a ticket with no workflow at all, which is complete. Before dispatch the
    instance is usually simply not created yet — a child filed by a stage agent
    minutes ago has none — and reading that as complete would say the parent has
    nothing left and send it to be dispatched, which is the loop this guard
    exists to stop. So here a missing instance means the work is still owed, and
    only an empty stage map (no workflow to run) is complete.
    """
    if ticket.state in (TicketState.DONE, TicketState.WONT_DO):
        return False
    _, stages = resolve_ticket_stages(session, ticket)
    if not stages:
        return False
    instance = workflow_instance_for(session, ticket.id)
    if instance is None:
        return True
    stage_map = parse_stage_map(instance, stages)
    return any(
        stage_map.get(stage.key, StageStatus.PENDING) not in (StageStatus.DONE, StageStatus.WONT_DO)
        for stage in stages
        if not stage.optional
    )


def _hold(
    session: Session,
    ticket: Ticket,
    workspace: Workspace | None,
    *,
    seen: set[str],
) -> str:
    # A parent cycle cannot be inserted, but reading one as infinite recursion
    # would take the tick down rather than report it.
    if ticket.id in seen:
        return f"parent cycle at {ticket.external_id}"
    seen = seen | {ticket.id}

    children = ordered_children(session, ticket)
    if not children:
        return ""
    incomplete = [c for c in children if has_work_left(session, c)]
    if not incomplete:
        # Every child is done: the parent finalizes on its next pass.
        return ""

    runnable, parked, waiting = partition_children(session, workspace, incomplete)
    deeper: list[str] = []
    for child in runnable:
        held = _hold(session, child, workspace, seen=seen)
        if not held:
            return ""
        deeper.append(f"{child.external_id}: {held}")
    if waiting:
        return render_hold_line(session, waiting)
    if parked:
        return f"Child ticket parked, awaiting a person: {', '.join(parked)}"
    if deeper:
        return "; ".join(deeper)
    # Every child was filtered out of the partition as non-workflow work.
    return ""
