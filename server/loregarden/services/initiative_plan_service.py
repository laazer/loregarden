"""An initiative's schedule: reading it whole, editing it, and the planner's proposals.

Three writers touch a schedule, and only two of them write targets:

* an **operator** edits targets, order and mode directly (`update_plan`);
* the **planner agent** never writes targets — it files a `ScheduleProposal`
  (`propose_schedule`), and a person accepting it (`accept_proposal`) is the
  write. A planner that could move committed dates on its own would make the
  baseline meaningless, which is the one thing a fixed plan is for.

Forecasts are not stored anywhere; `plan_view` computes them on every read
through `initiative_forecast`.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime

from loregarden.models.domain import (
    InitiativePlan,
    InitiativePlanUpdate,
    InitiativePlanView,
    ProposalSource,
    ProposalStatus,
    ScheduleProposal,
    ScheduleProposalCreate,
    ScheduleProposalView,
    ScheduleTarget,
    ScheduleTargetInput,
    Ticket,
    WorkItemType,
    utcnow,
)
from loregarden.services.initiative_forecast import (
    InitiativeForecaster,
    classify,
    drift,
    plan_mode,
    plan_sequence,
    planned,
)
from loregarden.services.initiative_service import milestones_under, workspace_slugs
from loregarden.services.ticket_state_service import RESOLVED_STATES
from pydantic import TypeAdapter
from sqlmodel import Session, col, select

logger = logging.getLogger(__name__)

_ITEMS = TypeAdapter(list[ScheduleTargetInput])


class ScheduleValidationError(ValueError):
    """A schedule edit or proposal named something it may not."""


def load_initiative(session: Session, initiative_id: str) -> Ticket:
    initiative = session.get(Ticket, initiative_id)
    if initiative is None or initiative.work_item_type != WorkItemType.INITIATIVE:
        raise LookupError(f"Initiative not found: {initiative_id}")
    return initiative


def _targets(session: Session, ticket_ids: list[str]) -> dict[str, ScheduleTarget]:
    if not ticket_ids:
        return {}
    rows = session.exec(select(ScheduleTarget).where(col(ScheduleTarget.ticket_id).in_(ticket_ids)))
    return {row.ticket_id: row for row in rows.all()}


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


def plan_view(
    session: Session, initiative_id: str, *, now: datetime | None = None
) -> InitiativePlanView:
    initiative = load_initiative(session, initiative_id)
    milestones = milestones_under(session, [initiative.id])
    plan = session.get(InitiativePlan, initiative.id)
    mode = plan_mode(plan)
    targets = _targets(session, [initiative.id, *(m.id for m in milestones)])
    forecaster = InitiativeForecaster(session, now=now)
    result = forecaster.forecast(milestones, workspace_slugs(session, milestones), targets, mode)

    own = targets.get(initiative.id)
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
        forecast_date=None if resolved else result.forecast_date,
        planned_date=planned(mode, target_date, result.forecast_date),
        drift_days=None if resolved else drift(target_date, result.forecast_date),
        status=classify(
            resolved=resolved,
            target=target_date,
            forecast=result.forecast_date,
            today=forecaster.now.date(),
        ),
        unforecast_milestones=result.unforecast_milestones,
        milestones=result.milestones,
        paces=result.paces,
        window_days=forecaster.window_days,
        pending_proposal=_proposal_view(pending) if pending is not None else None,
        generated_at=forecaster.now,
    )


def _validate_items(session: Session, initiative: Ticket, items: list[ScheduleTargetInput]) -> None:
    """Targets belong on the initiative and its milestones — nowhere else."""
    allowed = {initiative.id, *(m.id for m in milestones_under(session, [initiative.id]))}
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


def _reorder(
    sequences: list[tuple[str, list[Ticket]]], items: list[ScheduleTargetInput]
) -> list[list[Ticket]]:
    """Each workspace's sequence with the requested moves applied.

    ``plan_order=k`` means "at position k in its workspace", the others
    shifting to make room — a number written as-is would collide with the
    milestone already there. Moves apply lowest position first, so a request
    that lists a whole workspace's order reproduces it exactly.
    """
    moves = sorted(
        (item for item in items if item.plan_order is not None),
        key=lambda item: item.plan_order or 0,
    )
    result: list[list[Ticket]] = []
    for _, sequence in sequences:
        order = list(sequence)
        for item in moves:
            index = next((i for i, m in enumerate(order) if m.id == item.ticket_id), None)
            if index is None:
                continue
            moved = order.pop(index)
            order.insert(min(item.plan_order or 0, len(order)), moved)
        result.append(order)
    return result


def _apply_items(
    session: Session, initiative: Ticket, items: list[ScheduleTargetInput], *, actor: str
) -> None:
    """Write dates and order, keeping every milestone's order dense per workspace.

    Every milestone gets a row the first time anything is written: otherwise
    the first one dated would be the only one with an order, and would sort
    ahead of all the rest — setting a date would silently reorder the plan.
    """
    milestones = milestones_under(session, [initiative.id])
    rows = _targets(session, [initiative.id, *(m.id for m in milestones)])
    sequences = plan_sequence(milestones, rows, workspace_slugs(session, milestones))
    now = utcnow()
    for order in _reorder(sequences, items):
        for position, milestone in enumerate(order):
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


def _plan_row(session: Session, initiative_id: str) -> InitiativePlan:
    return session.get(InitiativePlan, initiative_id) or InitiativePlan(initiative_id=initiative_id)


def update_plan(
    session: Session, initiative_id: str, update: InitiativePlanUpdate, *, actor: str
) -> InitiativePlanView:
    initiative = load_initiative(session, initiative_id)
    _validate_items(session, initiative, update.targets)
    _apply_items(session, initiative, update.targets, actor=actor)
    if update.mode is not None or update.notes is not None:
        plan = _plan_row(session, initiative.id)
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
    plan = _plan_row(session, initiative.id)
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
