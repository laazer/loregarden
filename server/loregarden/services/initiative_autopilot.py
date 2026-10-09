"""Keep an initiative's lanes busy: start ready work as its prerequisites land.

Deterministic, not an agent turn: choosing the next ticket is mechanical once
the plan is a graph, and a model deciding it every minute would cost money to
reproduce a sort. The planner agent decides the *plan* — order, dates, what
needs a person; this executes it.

Each tick, per initiative with autopilot on:

1. Build the plan graph (`initiative_graph`).
2. Pick `READY` tickets in schedule order, at most one per plan lane in flight
   and at most `max_parallel` across the initiative. `NEEDS_PERSON` and
   `BLOCKED` tickets are never picked; neither is a prerequisite outside the
   initiative, which belongs to someone else's plan.
3. Re-check each pick against git (`unmet_prerequisites_for_start`): a
   prerequisite can be `done` with its work never landed, and the queue does
   not check prerequisites at all — a queued ticket starts when its slot frees.
4. Queue it the way the Queue page's "Add ticket" does
   (`QueueLaneService.add_to_lane`), into the least loaded agent slot, with
   `auto_approve` on: tool prompts do not stop it. Legacy stage sign-offs on
   what it started are answered by `autopilot_sign_off`; any other
   human-required exit action still goes to the inbox (AC-6).

**Circuit breaker.** When `BREAKER_LIMIT` tickets it started since it was last
turned on are blocked, it turns itself off and says why: something is wrong
with the work or the harness, and starting more only makes more of it.

Everything it does is an `AutopilotEvent`, shown on the page and returned to the
planner, so "why did this start?" always has an answer.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime

from loregarden.config import settings
from loregarden.db.session import engine
from loregarden.models.domain import (
    Approval,
    ApprovalKind,
    ApprovalStatus,
    AutopilotAction,
    AutopilotEvent,
    AutopilotEventView,
    AutopilotUpdate,
    AutopilotView,
    InitiativePlan,
    NodeStatus,
    Ticket,
    TicketState,
    Workspace,
    comparable_utc,
    utcnow,
)
from loregarden.services.autopilot_sign_off import (
    autopilot_may_sign_off,
    record_autopilot_sign_off,
    self_and_ancestors,
)
from loregarden.services.dependency_readiness import unmet_prerequisites_for_start
from loregarden.services.initiative_graph import (
    NEEDS_PERSON_TAG,
    PlanContext,
    PlanNode,
    build_plan,
)
from loregarden.services.initiative_service import load_initiative
from loregarden.services.orchestration import ApprovalService
from loregarden.services.queue_lanes import QueueLaneService, least_busy_lane
from loregarden.services.run_concurrency import find_active_orchestration_run
from loregarden.services.ticket_tags import load_tags, serialize_tags
from sqlmodel import Session, col, select

logger = logging.getLogger(__name__)

#: Tickets it started, now blocked, before it stops itself.
BREAKER_LIMIT = 3

#: Events shown on the page and to the planner.
RECENT_EVENTS = 8


def select_next(ctx: PlanContext, max_parallel: int) -> list[PlanNode]:
    """The ready tickets it would start now, in order."""
    graph = ctx.graph
    own = [n for n in graph.nodes.values() if not n.external]
    in_flight = [n for n in own if n.holds_lane]
    busy_lanes = {n.lane for n in in_flight}
    budget = max_parallel - len(in_flight)
    candidates = sorted(
        (n for n in own if n.status == NodeStatus.READY),
        # The critical path first — only work there brings the end date in —
        # then earlier phases, then whatever the schedule starts soonest.
        key=lambda n: (
            not n.critical,
            n.phase,
            n.start or graph.now,
            -n.rank,
            n.ticket.external_id,
        ),
    )
    picked: list[PlanNode] = []
    for node in candidates:
        if len(picked) >= budget:
            break
        if node.lane in busy_lanes:
            continue
        picked.append(node)
        busy_lanes.add(node.lane)
    return picked


def _record(
    session: Session,
    initiative_id: str,
    action: AutopilotAction,
    *,
    ticket_id: str | None = None,
    detail: str = "",
) -> AutopilotEvent:
    event = AutopilotEvent(
        initiative_id=initiative_id, action=action, ticket_id=ticket_id, detail=detail
    )
    session.add(event)
    return event


def _last_event_for(session: Session, initiative_id: str, ticket_id: str) -> AutopilotEvent | None:
    return session.exec(
        select(AutopilotEvent)
        .where(AutopilotEvent.initiative_id == initiative_id, AutopilotEvent.ticket_id == ticket_id)
        .order_by(col(AutopilotEvent.created_at).desc())
    ).first()


def _blocked_since_enabled(session: Session, plan: InitiativePlan) -> list[Ticket]:
    """Tickets it started since it was turned on that are now blocked."""
    since = plan.autopilot_since
    query = select(AutopilotEvent.ticket_id).where(
        AutopilotEvent.initiative_id == plan.initiative_id,
        AutopilotEvent.action == AutopilotAction.DISPATCHED,
    )
    if since is not None:
        query = query.where(AutopilotEvent.created_at >= since)
    ids = {tid for tid in session.exec(query).all() if tid}
    if not ids:
        return []
    return list(
        session.exec(
            select(Ticket).where(col(Ticket.id).in_(ids), Ticket.state == TicketState.BLOCKED)
        ).all()
    )


def _pause(session: Session, plan: InitiativePlan, reason: str) -> None:
    plan.autopilot = False
    plan.paused_reason = reason
    plan.updated_at = utcnow()
    session.add(plan)
    _record(session, plan.initiative_id, AutopilotAction.PAUSED, detail=reason)
    session.commit()
    logger.warning("Autopilot paused for initiative %s: %s", plan.initiative_id, reason)


def _dispatch(
    session: Session, plan: InitiativePlan, node: PlanNode, lanes: QueueLaneService
) -> bool:
    """Queue one pick, or record why not. True when it was queued."""
    ticket = node.ticket
    if settings.sandbox:
        # A sandbox runs on a copy of the database, but a queued run works in
        # the real repositories: it would cut worktrees and commit for real.
        _record(
            session,
            plan.initiative_id,
            AutopilotAction.REFUSED,
            ticket_id=ticket.id,
            detail="sandbox server: no agent runs are started here",
        )
        return False
    workspace = session.get(Workspace, ticket.workspace_id) if ticket.workspace_id else None
    if workspace is None:
        _record(
            session,
            plan.initiative_id,
            AutopilotAction.REFUSED,
            ticket_id=ticket.id,
            detail="ticket has no workspace",
        )
        return False
    unmet = unmet_prerequisites_for_start(session, ticket, workspace)
    if unmet:
        detail = "waiting for prerequisite work to land: " + ", ".join(
            f"{u.ticket.external_id} ({u.reason.value}{' on ' + u.target if u.target else ''})"
            for u in unmet
        )
        last = _last_event_for(session, plan.initiative_id, ticket.id)
        # Said once, not every minute it stays true.
        if last is None or last.detail != detail:
            _record(
                session,
                plan.initiative_id,
                AutopilotAction.REFUSED,
                ticket_id=ticket.id,
                detail=detail,
            )
        return False
    try:
        slot = least_busy_lane(lanes)
        result = lanes.add_to_lane(ticket_id=ticket.id, slot_number=slot, auto_approve=True)
    except ValueError as exc:
        logger.warning("Autopilot could not queue %s: %s", ticket.external_id, exc)
        _record(
            session,
            plan.initiative_id,
            AutopilotAction.REFUSED,
            ticket_id=ticket.id,
            detail=str(exc),
        )
        return False
    _record(
        session,
        plan.initiative_id,
        AutopilotAction.DISPATCHED,
        ticket_id=ticket.id,
        detail=f"lane {node.lane} → agent slot {slot} ({result.get('status', 'queued')})",
    )
    return True


def _sign_off_parked_gates(session: Session, plan: InitiativePlan) -> set[str]:
    """Answer the legacy gates left waiting on work it started.

    Returns the ids not to queue this tick: each answered ticket and its ancestors.

    Run completion signs these as they are raised. This catches one raised while
    the autopilot was off, or before it could sign: nothing else re-enters the
    workflow for a parked ticket. Approving resumes the workflow on its own.
    """
    # Every pending gate, not only those on tickets it queued: it queues a
    # parent and the gate opens on a child. The inbox is small, and
    # `autopilot_may_sign_off` decides which of these are this plan's.
    rows = session.exec(
        select(Approval, Ticket)
        .join(Ticket, col(Ticket.id) == col(Approval.ticket_id))
        .where(
            Approval.status == ApprovalStatus.PENDING,
            Approval.kind == ApprovalKind.WORKFLOW_GATE,
        )
    ).all()
    answered: set[str] = set()
    for approval, ticket in rows:
        # A live orchestration answers its own gate in its loop; resolving it
        # here too races that resolve into "already resolved".
        if find_active_orchestration_run(session, ticket.id) is not None:
            continue
        driver = autopilot_may_sign_off(session, ticket, approval)
        if driver is None or driver.initiative_id != plan.initiative_id:
            continue
        try:
            ApprovalService(session).resolve(approval.id, approved=True)
        except ValueError as exc:
            # The gate stays in the inbox for a person, and the log says why —
            # once, not every tick it stays true.
            detail = f"could not approve the '{approval.stage_key}' gate: {exc}"
            last = _last_event_for(session, plan.initiative_id, ticket.id)
            if last is None or last.detail != detail:
                logger.warning("Autopilot could not sign off gate %s: %s", approval.id, exc)
                _record(
                    session,
                    plan.initiative_id,
                    AutopilotAction.REFUSED,
                    ticket_id=ticket.id,
                    detail=detail,
                )
                session.commit()
            continue
        session.refresh(approval)
        record_autopilot_sign_off(session, ticket, driver, approval)
        # Its parents too: their orchestration would run it a second time.
        answered.update(self_and_ancestors(session, ticket))
    return answered


def run_autopilot(session: Session, initiative_id: str, *, now: datetime | None = None) -> int:
    """One tick for one initiative. Returns how many tickets it queued."""
    plan = session.get(InitiativePlan, initiative_id)
    if plan is None or not plan.autopilot:
        return 0
    blocked = _blocked_since_enabled(session, plan)
    if len(blocked) >= BREAKER_LIMIT:
        _pause(
            session,
            plan,
            f"{len(blocked)} tickets it started are blocked "
            f"({', '.join(t.external_id for t in blocked[:5])}); look at them, then turn it back on",
        )
        return 0
    # The resume a sign-off schedules starts on another thread, so this tick
    # would read those tickets idle and queue them a second time.
    resuming = _sign_off_parked_gates(session, plan)
    ctx = build_plan(session, load_initiative(session, initiative_id), now=now or utcnow())
    lanes = QueueLaneService(session)
    dispatched = 0
    for node in select_next(ctx, plan.max_parallel):
        if node.ticket.id in resuming:
            continue
        dispatched += int(_dispatch(session, plan, node, lanes))
        session.commit()
    return dispatched


def set_autopilot(
    session: Session, initiative_id: str, update: AutopilotUpdate, *, actor: str
) -> InitiativePlan:
    load_initiative(session, initiative_id)
    plan = session.get(InitiativePlan, initiative_id) or InitiativePlan(initiative_id=initiative_id)
    if update.max_parallel is not None:
        plan.max_parallel = update.max_parallel
    if update.enabled is not None and update.enabled != plan.autopilot:
        plan.autopilot = update.enabled
        if update.enabled:
            plan.autopilot_since = utcnow()
            plan.paused_reason = ""
        _record(
            session,
            initiative_id,
            AutopilotAction.ENABLED if update.enabled else AutopilotAction.DISABLED,
            detail=f"by {actor}",
        )
    plan.updated_at = utcnow()
    session.add(plan)
    session.commit()
    session.refresh(plan)
    return plan


def autopilot_view(
    session: Session, ctx: PlanContext, plan: InitiativePlan | None
) -> AutopilotView:
    max_parallel = plan.max_parallel if plan is not None else InitiativePlan().max_parallel
    events = session.exec(
        select(AutopilotEvent)
        .where(AutopilotEvent.initiative_id == ctx.initiative.id)
        .order_by(col(AutopilotEvent.created_at).desc())
        .limit(RECENT_EVENTS)
    ).all()
    ids = {e.ticket_id for e in events if e.ticket_id}
    external = (
        dict(
            session.exec(select(Ticket.id, Ticket.external_id).where(col(Ticket.id).in_(ids))).all()
        )
        if ids
        else {}
    )
    return AutopilotView(
        enabled=bool(plan and plan.autopilot),
        max_parallel=max_parallel,
        paused_reason=plan.paused_reason if plan is not None else "",
        in_flight=sum(1 for n in ctx.graph.nodes.values() if n.holds_lane and not n.external),
        next_up=[n.id for n in select_next(ctx, max_parallel)],
        available=not settings.sandbox,
        recent=[
            AutopilotEventView(
                action=e.action,
                ticket_id=e.ticket_id,
                ticket_external_id=external.get(e.ticket_id or ""),
                detail=e.detail,
                created_at=comparable_utc(e.created_at),
            )
            for e in events
        ],
    )


# ---- the clock -----------------------------------------------------------


def _tick() -> int:
    """Every initiative with autopilot on, each in its own session."""
    with Session(engine) as session:
        ids = list(
            session.exec(
                select(InitiativePlan.initiative_id).where(col(InitiativePlan.autopilot).is_(True))
            ).all()
        )
    total = 0
    for initiative_id in ids:
        try:
            with Session(engine) as session:
                total += run_autopilot(session, initiative_id)
        except Exception:  # noqa: BLE001 — one initiative's failure must not stop the others
            logger.exception("Autopilot tick failed for initiative %s; continuing", initiative_id)
    return total


async def run_autopilot_loop(interval_seconds: float) -> None:
    while True:
        try:
            await asyncio.sleep(interval_seconds)
            await asyncio.to_thread(_tick)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 — the loop outliving a bad tick is the point
            logger.exception("Autopilot loop iteration failed; continuing")


def start_autopilot_loop(interval_seconds: float | None = None) -> asyncio.Task | None:
    """Start the loop, or None when the interval is non-positive (the off switch)."""
    if interval_seconds is None:
        interval_seconds = settings.autopilot_interval_seconds
    if interval_seconds <= 0:
        return None
    return asyncio.create_task(run_autopilot_loop(interval_seconds), name="initiative-autopilot")


# ---- what the planner agent can do directly ------------------------------


def _plan_nodes(session: Session, initiative_id: str) -> tuple[PlanContext, InitiativePlan]:
    ctx = build_plan(session, load_initiative(session, initiative_id), now=utcnow())
    plan = session.get(InitiativePlan, initiative_id) or InitiativePlan(initiative_id=initiative_id)
    return ctx, plan


def start_ready_work(
    session: Session, initiative_id: str, ticket_ids: list[str], *, actor: str
) -> dict[str, str]:
    """Queue named tickets now, if — and only if — the plan says they are ready.

    The same checks and the same queue path as the autopilot, so an agent
    asking for a ticket by name cannot start one that is waiting on a person
    or on unfinished prerequisites. Returns ticket id -> what happened.
    """
    ctx, plan = _plan_nodes(session, initiative_id)
    lanes = QueueLaneService(session)
    outcome: dict[str, str] = {}
    for ticket_id in ticket_ids:
        node = ctx.graph.nodes.get(ticket_id)
        if node is None or node.external:
            outcome[ticket_id] = "not part of this initiative's plan"
        elif node.status != NodeStatus.READY:
            waiting = ", ".join(ctx.graph.nodes[d].ticket.external_id for d in node.waiting_on)
            outcome[ticket_id] = f"not ready: {node.status.value}" + (
                f" (waiting on {waiting})" if waiting else ""
            )
        else:
            queued = _dispatch(session, plan, node, lanes)
            outcome[ticket_id] = "queued" if queued else "refused — see the autopilot log"
        session.commit()
    logger.info("start_ready_work by %s on %s: %s", actor, initiative_id, outcome)
    return outcome


def mark_needs_person(
    session: Session, initiative_id: str, ticket_ids: list[str], *, needs_person: bool
) -> list[str]:
    """Tag (or untag) plan tickets as needing a person, so autopilot leaves them alone."""
    ctx, _ = _plan_nodes(session, initiative_id)
    unknown = [tid for tid in ticket_ids if tid not in ctx.graph.nodes]
    if unknown:
        raise ValueError(f"Not in this initiative's plan: {', '.join(unknown)}")
    changed: list[str] = []
    for ticket_id in ticket_ids:
        ticket = ctx.graph.nodes[ticket_id].ticket
        tags = load_tags(ticket.tags_json)
        has = NEEDS_PERSON_TAG in tags
        if needs_person == has:
            continue
        tags = (
            [*tags, NEEDS_PERSON_TAG]
            if needs_person
            else [t for t in tags if t != NEEDS_PERSON_TAG]
        )
        ticket.tags_json = serialize_tags(tags)
        ticket.revision += 1
        ticket.updated_at = utcnow()
        session.add(ticket)
        changed.append(ticket.external_id)
    session.commit()
    return changed
