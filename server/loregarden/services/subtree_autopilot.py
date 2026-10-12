"""The autopilot as a milestone or parent ticket's card shows it.

The engine is `initiative_autopilot`'s, rooted at the parent
(`load_plan_root`); this module only reads it into the shape the card needs:
what is running, what starts next, what waits on a person, and whether
something above is already driving this subtree.
"""

from __future__ import annotations

from loregarden.models.domain import (
    AutopilotTicketRef,
    InitiativePlan,
    NodeStatus,
    SubtreeAutopilotView,
    Ticket,
    utcnow,
)
from loregarden.services.autopilot_sign_off import self_and_ancestors
from loregarden.services.initiative_autopilot import autopilot_view
from loregarden.services.initiative_coverage import initiatives_tracking
from loregarden.services.initiative_graph import PlanNode, build_plan
from loregarden.services.initiative_service import load_plan_root
from loregarden.services.run_concurrency import find_active_orchestration_run
from sqlmodel import Session, col, select


def _ref(ticket: Ticket) -> AutopilotTicketRef:
    return AutopilotTicketRef(id=ticket.id, external_id=ticket.external_id, title=ticket.title)


def _running_above(session: Session, lineage: list[str]) -> AutopilotTicketRef | None:
    for ticket_id in lineage:
        if find_active_orchestration_run(session, ticket_id) is not None:
            ticket = session.get(Ticket, ticket_id)
            return _ref(ticket) if ticket is not None else None
    return None


def _also_driven_by(session: Session, root: Ticket, lineage: list[str]) -> list[AutopilotTicketRef]:
    """Plans above this one, by parentage or initiative membership, with autopilot on."""
    above = set(lineage[1:]) | set(initiatives_tracking(session, lineage))
    if not above:
        return []
    rows = session.exec(
        select(Ticket)
        .join(InitiativePlan, col(InitiativePlan.initiative_id) == col(Ticket.id))
        .where(col(Ticket.id).in_(above), col(InitiativePlan.autopilot).is_(True))
        .order_by(col(Ticket.external_id))
    ).all()
    return [_ref(t) for t in rows if t.id != root.id]


def subtree_autopilot_view(session: Session, ticket_id: str) -> SubtreeAutopilotView:
    root = load_plan_root(session, ticket_id)
    ctx = build_plan(session, root, now=utcnow())
    plan = session.get(InitiativePlan, root.id)
    view = autopilot_view(session, ctx, plan)
    own: list[PlanNode] = [
        n for n in ctx.graph.nodes.values() if not n.external and n.id != root.id
    ]
    nodes = ctx.graph.nodes
    lineage = self_and_ancestors(session, root)
    return SubtreeAutopilotView(
        root_id=root.id,
        autopilot=view,
        total_items=len(own),
        open_items=sum(1 for n in own if n.status != NodeStatus.DONE),
        next_up=[_ref(nodes[i].ticket) for i in view.next_up if i in nodes],
        waiting_on_person=[_ref(n.ticket) for n in own if n.status == NodeStatus.NEEDS_PERSON],
        blocked=[_ref(n.ticket) for n in own if n.status == NodeStatus.BLOCKED],
        running_above=_running_above(session, lineage),
        also_driven_by=_also_driven_by(session, root, lineage),
    )
