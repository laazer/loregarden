"""An initiative's schedule: reading it whole, editing it, and the planner's proposals.

Three writers touch a schedule, and only two of them write targets:

* an **operator** edits targets, order and mode directly (`update_plan`);
* the **planner agent** never writes targets — it files a `ScheduleProposal`
  (`propose_schedule`), and a person accepting it (`accept_proposal`) is the
  write. A planner that could move committed dates on its own would make the
  baseline meaningless, which is the one thing a fixed plan is for.

Forecasts are not stored anywhere; `plan_view` computes them on every read
from the plan's dependency graph (`initiative_graph`).
"""

from __future__ import annotations

import json
import logging
from collections import Counter
from datetime import date, datetime

from loregarden.models.domain import (
    ForecastBasis,
    InitiativePlan,
    InitiativePlanUpdate,
    InitiativePlanView,
    MilestoneSchedule,
    NodeStatus,
    PlanNodeView,
    ProposalSource,
    ProposalStatus,
    ScheduleMode,
    ScheduleProposal,
    ScheduleProposalCreate,
    ScheduleProposalView,
    ScheduleStatus,
    ScheduleTarget,
    ScheduleTargetInput,
    Ticket,
    WorkspacePace,
    utcnow,
)
from loregarden.services.initiative_autopilot import autopilot_view
from loregarden.services.initiative_forecast import (
    WINDOW_DAYS,
    classify,
    drift,
    plan_mode,
    plan_sequence,
    planned,
    target_in_force,
)
from loregarden.services.initiative_graph import PlanContext, PlanNode, build_plan
from loregarden.services.initiative_service import (
    load_initiative,
    load_targets,
    milestones_under,
)
from loregarden.services.ticket_state_service import RESOLVED_STATES
from pydantic import TypeAdapter
from sqlmodel import Session, col, select

logger = logging.getLogger(__name__)

_ITEMS = TypeAdapter(list[ScheduleTargetInput])

# Re-exported: callers read an initiative through this module.
__all__ = ["load_initiative", "plan_view"]


class ScheduleValidationError(ValueError):
    """A schedule edit or proposal named something it may not."""


_targets = load_targets


def _pending_proposal(session: Session, initiative_id: str) -> ScheduleProposal | None:
    return session.exec(
        select(ScheduleProposal)
        .where(
            ScheduleProposal.initiative_id == initiative_id,
            ScheduleProposal.status == ProposalStatus.PENDING,
        )
        .order_by(col(ScheduleProposal.created_at).desc())
    ).first()


def _proposal_view(proposal: ScheduleProposal) -> ScheduleProposalView:
    return ScheduleProposalView(
        id=proposal.id,
        source=proposal.source,
        mode=proposal.mode,
        rationale=proposal.rationale,
        items=[
            item.model_dump(mode="json", exclude_unset=True)
            for item in _ITEMS.validate_json(proposal.items_json)
        ],
        created_at=proposal.created_at,
    )


def _latest(nodes: list[PlanNode]) -> tuple[date | None, PlanNode | None]:
    """When the last of `nodes` finishes; (None, None) if any finish is unknown."""
    if any(n.finish is None for n in nodes):
        return None, None
    last = max(nodes, key=lambda n: n.finish or datetime.min, default=None)
    return (last.finish.date() if last is not None and last.finish else None), last


def _milestone_views(ctx: PlanContext, mode: ScheduleMode, today: date) -> list[MilestoneSchedule]:
    views: list[MilestoneSchedule] = []
    for position, milestone in enumerate(plan_sequence(ctx.milestones, ctx.targets)):
        nodes = ctx.graph.for_milestone(milestone.id)
        open_nodes = [n for n in nodes if n.status != NodeStatus.DONE]
        resolved = milestone.state in RESOLVED_STATES
        target = ctx.targets.get(milestone.id)
        target_date = target.target_date if target is not None else None
        if resolved:
            forecast, last = None, None
        elif not open_nodes:
            # Every item is closed and the rollup has not caught up yet.
            forecast, last = today, None
        else:
            forecast, last = _latest(open_nodes)
        views.append(
            MilestoneSchedule(
                id=milestone.id,
                external_id=milestone.external_id,
                title=milestone.title,
                state=milestone.state,
                workspace_slug=ctx.slugs.get(milestone.workspace_id or "", ""),
                work_item_type=milestone.work_item_type,
                member=milestone.id in ctx.member_ids,
                plan_order=position,
                target_date=target_date,
                forecast_date=forecast,
                planned_date=planned(mode, target_date, forecast),
                drift_days=None
                if resolved
                else drift(target_in_force(mode, target_date), forecast),
                status=classify(
                    mode=mode, resolved=resolved, target=target_date, forecast=forecast, today=today
                ),
                basis=last.basis if last is not None else ForecastBasis.NONE,
                remaining=len(open_nodes),
                total=len(nodes),
                counts=dict(Counter(n.status for n in open_nodes)),
                assumed=sum(1 for n in open_nodes if n.assumed),
            )
        )
    return views


def _node_view(ctx: PlanContext, node: PlanNode) -> PlanNodeView:
    ticket = node.ticket
    return PlanNodeView(
        id=ticket.id,
        external_id=ticket.external_id,
        title=ticket.title,
        workspace_slug=ctx.slugs.get(ticket.workspace_id or "", ""),
        state=ticket.state,
        status=node.status,
        lane=node.lane,
        milestone_id=node.milestone_id,
        step=node.step,
        deps=node.deps,
        waiting_on=node.waiting_on,
        start=node.start,
        finish=node.finish,
        duration_days=round(node.duration_days, 2) if node.duration_days is not None else None,
        basis=node.basis,
        assumed=node.assumed,
        critical=node.critical,
        external=node.external,
    )


def plan_view(
    session: Session, initiative_id: str, *, now: datetime | None = None
) -> InitiativePlanView:
    initiative = load_initiative(session, initiative_id)
    now = now or utcnow()
    today = now.date()
    ctx = build_plan(session, initiative, now=now)
    plan = session.get(InitiativePlan, initiative.id)
    mode = plan_mode(plan)
    milestones = _milestone_views(ctx, mode, today)

    own_open = [
        n for n in ctx.graph.nodes.values() if not n.external and n.status != NodeStatus.DONE
    ]
    unforecast = sum(
        1 for m in milestones if m.status != ScheduleStatus.DONE and m.forecast_date is None
    )
    forecast_date, _ = _latest(own_open) if own_open else (today, None)
    if unforecast:
        forecast_date = None

    own = ctx.targets.get(initiative.id)
    target_date = own.target_date if own is not None else None
    resolved = initiative.state in RESOLVED_STATES
    pending = _pending_proposal(session, initiative.id)
    return InitiativePlanView(
        id=initiative.id,
        external_id=initiative.external_id,
        title=initiative.title,
        description=initiative.description,
        state=initiative.state,
        mode=mode,
        notes=plan.notes if plan is not None else "",
        target_date=target_date,
        forecast_date=None if resolved else forecast_date,
        planned_date=planned(mode, target_date, forecast_date),
        drift_days=None if resolved else drift(target_in_force(mode, target_date), forecast_date),
        status=classify(
            mode=mode, resolved=resolved, target=target_date, forecast=forecast_date, today=today
        ),
        unforecast_milestones=unforecast,
        milestones=milestones,
        paces=[
            WorkspacePace(
                workspace_slug=ctx.slugs.get(workspace_id, ""),
                per_day=pace.per_day,
                completed=pace.completed,
                basis=pace.basis,
            )
            for workspace_id, pace in sorted(
                ctx.paces.items(), key=lambda kv: ctx.slugs.get(kv[0], "")
            )
        ],
        window_days=WINDOW_DAYS,
        pending_proposal=_proposal_view(pending) if pending is not None else None,
        nodes=[_node_view(ctx, n) for n in ctx.graph.nodes.values()],
        critical_path=ctx.graph.critical_path,
        cyclic=ctx.graph.cyclic,
        lanes=ctx.graph.lanes,
        autopilot=autopilot_view(session, ctx, plan),
        generated_at=now,
    )


def _validate_items(session: Session, initiative: Ticket, items: list[ScheduleTargetInput]) -> None:
    """Targets belong on the initiative and its phases (children, members) — nowhere else."""
    allowed = {initiative.id, *(m.id for m in milestones_under(session, initiative.id))}
    unknown = sorted({item.ticket_id for item in items} - allowed)
    if unknown:
        raise ScheduleValidationError(
            f"Not a milestone of initiative {initiative.external_id}: {', '.join(unknown)}"
        )
    seen: set[str] = set()
    for item in items:
        if item.ticket_id in seen:
            raise ScheduleValidationError(f"Ticket listed twice: {item.ticket_id}")
        seen.add(item.ticket_id)
        if item.plan_order is not None and item.plan_order < 0:
            raise ScheduleValidationError(f"plan_order must be >= 0: {item.ticket_id}")


def _reorder(sequence: list[Ticket], items: list[ScheduleTargetInput]) -> list[Ticket]:
    """The milestone sequence with the requested moves applied.

    ``plan_order=k`` means "at position k", the others shifting to make room —
    a number written as-is would collide with the milestone already there.
    Moves apply lowest position first, so a request that lists the whole order
    reproduces it exactly.
    """
    order = list(sequence)
    moves = sorted(
        (item for item in items if item.plan_order is not None),
        key=lambda item: item.plan_order or 0,
    )
    for item in moves:
        index = next((i for i, m in enumerate(order) if m.id == item.ticket_id), None)
        if index is None:
            continue
        moved = order.pop(index)
        order.insert(min(item.plan_order or 0, len(order)), moved)
    return order


def _apply_items(
    session: Session, initiative: Ticket, items: list[ScheduleTargetInput], *, actor: str
) -> None:
    """Write dates and order, keeping every milestone's order dense.

    Every milestone gets a row the first time anything is written: otherwise
    the first one dated would be the only one with an order, and would sort
    ahead of all the rest — setting a date would silently reorder the plan.
    """
    milestones = milestones_under(session, initiative.id)
    rows = _targets(session, [initiative.id, *(m.id for m in milestones)])
    now = utcnow()
    for position, milestone in enumerate(_reorder(plan_sequence(milestones, rows), items)):
        row = rows.get(milestone.id)
        if row is None:
            row = rows[milestone.id] = ScheduleTarget(ticket_id=milestone.id, updated_by=actor)
        elif row.plan_order == position:
            continue
        row.plan_order = position
        session.add(row)
    for item in items:
        if "target_date" not in item.model_fields_set:
            continue
        # Omitted leaves the date alone; an explicit null clears it. A reorder
        # must not wipe the dates it did not mention.
        row = rows.get(item.ticket_id)
        if row is None:  # the initiative's own target, written for the first time
            row = rows[item.ticket_id] = ScheduleTarget(ticket_id=item.ticket_id)
        row.target_date = item.target_date
        row.updated_by = actor
        row.updated_at = now
        session.add(row)


def plan_row(session: Session, initiative_id: str) -> InitiativePlan:
    return session.get(InitiativePlan, initiative_id) or InitiativePlan(initiative_id=initiative_id)


def update_plan(
    session: Session, initiative_id: str, update: InitiativePlanUpdate, *, actor: str
) -> InitiativePlanView:
    initiative = load_initiative(session, initiative_id)
    _validate_items(session, initiative, update.targets)
    _apply_items(session, initiative, update.targets, actor=actor)
    if update.mode is not None or update.notes is not None:
        plan = plan_row(session, initiative.id)
        if update.mode is not None:
            plan.mode = update.mode
        if update.notes is not None:
            plan.notes = update.notes
        plan.updated_at = utcnow()
        session.add(plan)
    session.commit()
    return plan_view(session, initiative.id)


def propose_schedule(
    session: Session,
    initiative_id: str,
    body: ScheduleProposalCreate,
    *,
    source: ProposalSource,
) -> ScheduleProposal:
    """File a proposal, superseding any still pending for the initiative."""
    initiative = load_initiative(session, initiative_id)
    if not body.items and body.mode is None:
        raise ScheduleValidationError("A proposal must change at least one target or the mode")
    _validate_items(session, initiative, body.items)
    previous = _pending_proposal(session, initiative.id)
    now = utcnow()
    if previous is not None:
        previous.status = ProposalStatus.SUPERSEDED
        previous.resolved_at = now
        session.add(previous)
    proposal = ScheduleProposal(
        initiative_id=initiative.id,
        source=source,
        mode=body.mode,
        rationale=body.rationale.strip(),
        # exclude_unset keeps "omitted" distinct from "null" through storage.
        items_json=_ITEMS.dump_json(body.items, exclude_unset=True).decode(),
        created_at=now,
    )
    session.add(proposal)
    session.commit()
    session.refresh(proposal)
    return proposal


def _resolve_pending(session: Session, initiative_id: str, proposal_id: str) -> ScheduleProposal:
    proposal = session.get(ScheduleProposal, proposal_id)
    if proposal is None or proposal.initiative_id != initiative_id:
        raise LookupError(f"Schedule proposal not found: {proposal_id}")
    if proposal.status != ProposalStatus.PENDING:
        raise ScheduleValidationError(f"Proposal is already {proposal.status.value}")
    return proposal


def accept_proposal(
    session: Session, initiative_id: str, proposal_id: str, *, actor: str
) -> InitiativePlanView:
    initiative = load_initiative(session, initiative_id)
    proposal = _resolve_pending(session, initiative.id, proposal_id)
    items = _ITEMS.validate_json(proposal.items_json)
    # Re-checked: a milestone can be detached between proposing and accepting.
    _validate_items(session, initiative, items)
    _apply_items(session, initiative, items, actor=actor)
    plan = plan_row(session, initiative.id)
    if proposal.mode is not None:
        plan.mode = proposal.mode
    if proposal.rationale:
        plan.notes = proposal.rationale
    plan.updated_at = utcnow()
    session.add(plan)
    proposal.status = ProposalStatus.ACCEPTED
    proposal.resolved_at = utcnow()
    session.add(proposal)
    session.commit()
    logger.info(
        "Schedule proposal %s accepted for %s by %s", proposal.id, initiative.external_id, actor
    )
    return plan_view(session, initiative.id)


def discard_proposal(session: Session, initiative_id: str, proposal_id: str) -> InitiativePlanView:
    initiative = load_initiative(session, initiative_id)
    proposal = _resolve_pending(session, initiative.id, proposal_id)
    proposal.status = ProposalStatus.DISCARDED
    proposal.resolved_at = utcnow()
    session.add(proposal)
    session.commit()
    return plan_view(session, initiative.id)


def plan_payload(view: InitiativePlanView) -> str:
    """The plan as the planner agent reads it."""
    return json.dumps(view.model_dump(mode="json"), indent=2)
