"""The parallel-execution snapshot, in one place.

Both the REST status endpoint and the queue websocket answer the same
question, and a client that switches between them must not see a different
shape depending on which one replied — so neither builds the payload itself.
"""

from __future__ import annotations

from dataclasses import asdict
from datetime import datetime
from typing import Any

from loregarden.models.domain import (
    AgentRun,
    OrchestrationRun,
    RunStatus,
    StudioAgent,
    Ticket,
    Workspace,
)
from loregarden.services.parallel_queue import (
    LIVE_ORCHESTRATION_STATUSES,
    ParallelQueueService,
)
from loregarden.services.queue_history import QueueHistoryEntry, QueueHistoryService
from loregarden.services.run_duration_stats import (
    DurationStats,
    load_duration_stats,
    project_clear_time,
    project_lane_waits,
)
from loregarden.services.ticket_activity import classify_ticket_activity
from loregarden.services.ticket_tree_estimate import TicketTreeEstimator
from sqlmodel import Session, col, select

#: Slots in the shared pool. Matches the default the REST endpoint has always
#: used. Not per workspace — see parallel_queue for why that changed.
DEFAULT_MAX_CONCURRENT = 3

_LIVE_AGENT_STATUSES = (RunStatus.RUNNING, RunStatus.AWAITING_PERMISSION)


def _ticket_ref(ticket: Ticket) -> dict[str, str]:
    work_item_type = ticket.work_item_type
    return {
        "id": ticket.id,
        "code": ticket.external_id or "",
        "title": ticket.title or "",
        "work_item_type": (
            work_item_type.value if hasattr(work_item_type, "value") else str(work_item_type or "")
        ),
    }


def _load_tickets_with_ancestors(session: Session, ticket_ids: set[str]) -> dict[str, Ticket]:
    """Batch-load tickets and walk every parent chain without N+1 lookups."""
    tickets: dict[str, Ticket] = {}
    pending = set(ticket_ids)
    while pending:
        rows = session.exec(select(Ticket).where(col(Ticket.id).in_(pending))).all()
        pending = set()
        for ticket in rows:
            tickets[ticket.id] = ticket
            parent_id = ticket.parent_ticket_id
            if parent_id and parent_id not in tickets:
                pending.add(parent_id)
    return tickets


def _ancestry_chain(ticket: Ticket, by_id: dict[str, Ticket]) -> list[dict[str, str]]:
    """Root → ticket, matching the work-items tree path for a lane card."""
    chain: list[Ticket] = []
    current: Ticket | None = ticket
    seen: set[str] = set()
    while current and current.id not in seen:
        seen.add(current.id)
        chain.append(current)
        parent_id = current.parent_ticket_id
        current = by_id.get(parent_id) if parent_id else None
    chain.reverse()
    return [_ticket_ref(node) for node in chain]


def _descendant_depth(ticket_id: str, root_id: str, by_id: dict[str, Ticket]) -> int | None:
    """Steps from ``root_id`` down to ``ticket_id``, or None when not under root."""
    if ticket_id == root_id:
        return 0
    depth = 0
    current = by_id.get(ticket_id)
    seen: set[str] = set()
    while current and current.id not in seen:
        seen.add(current.id)
        parent_id = current.parent_ticket_id
        if not parent_id:
            return None
        depth += 1
        if parent_id == root_id:
            return depth
        current = by_id.get(parent_id)
    return None


def _live_work_ticket_ids(session: Session) -> set[str]:
    """Tickets with a live orchestration or agent run — nested work under a lane."""
    live: set[str] = set(
        session.exec(
            select(OrchestrationRun.ticket_id).where(
                col(OrchestrationRun.status).in_(LIVE_ORCHESTRATION_STATUSES)
            )
        ).all()
    )
    live.update(
        session.exec(
            select(AgentRun.ticket_id).where(col(AgentRun.status).in_(_LIVE_AGENT_STATUSES))
        ).all()
    )
    return {ticket_id for ticket_id in live if ticket_id}


def _label_runs(session: Session, runs: list[dict[str, Any]]) -> None:
    """Attach the names behind the ids, in place.

    The queue tables carry ids because that is what scheduling needs; a slot
    card showing a bare uuid is not something anyone can read. Batched selects
    for the whole snapshot — a lookup per run would add an N+1 on the path the
    websocket walks every five seconds.

    ``ticket_activity`` rides along because the queue's own bookkeeping only
    knows about slots: it can say a ticket holds one, not whether an agent is
    still on it. A card that reads the two together cannot claim a lane is
    working when the run behind it has ended.

    ``ticket_ancestry`` / ``running_descendant`` answer the nested-tree case: a
    parent holds the lane while a child execute is what is actually moving, and
    a bare title cannot tell those apart the way the work-items tree can.
    """
    ticket_ids = {run["ticket_id"] for run in runs if run.get("ticket_id")}
    agent_slugs = {run["agent_id"] for run in runs if run.get("agent_id")}
    workspace_ids = {run["workspace_id"] for run in runs if run.get("workspace_id")}

    tickets = _load_tickets_with_ancestors(session, ticket_ids) if ticket_ids else {}

    live_ids = _live_work_ticket_ids(session) if ticket_ids else set()
    missing_live = live_ids - tickets.keys()
    if missing_live:
        tickets.update(_load_tickets_with_ancestors(session, missing_live))

    agents: dict[str, StudioAgent] = {}
    if agent_slugs:
        rows = session.exec(select(StudioAgent).where(StudioAgent.slug.in_(agent_slugs))).all()
        agents = {agent.slug: agent for agent in rows}

    workspaces: dict[str, Workspace] = {}
    if workspace_ids:
        rows = session.exec(select(Workspace).where(Workspace.id.in_(workspace_ids))).all()
        workspaces = {workspace.id: workspace for workspace in rows}

    activity = classify_ticket_activity(session, ticket_ids)

    for run in runs:
        ticket = tickets.get(run.get("ticket_id", ""))
        run["ticket_title"] = ticket.title if ticket else ""
        run["ticket_code"] = ticket.external_id if ticket else ""
        run["ticket_state"] = ticket.state.value if ticket else ""
        run["ticket_activity"] = (
            activity[run["ticket_id"]].value if run.get("ticket_id") in activity else ""
        )
        run["ticket_ancestry"] = _ancestry_chain(ticket, tickets) if ticket else []

        root_id = run.get("ticket_id") or ""
        best: Ticket | None = None
        best_depth = 0
        for live_id in live_ids:
            depth = _descendant_depth(live_id, root_id, tickets)
            if depth is not None and depth > best_depth:
                best_depth = depth
                best = tickets.get(live_id)
        run["running_descendant"] = _ticket_ref(best) if best else None

        agent = agents.get(run.get("agent_id", ""))
        # Falling back to the slug is not a placeholder: a run whose agent
        # definition was deleted still has a meaningful slug to show.
        run["agent_name"] = agent.name if agent else run.get("agent_id", "")

        # The pool is shared, so a card has to say whose work it is — without
        # this the board shows three runs and no way to tell them apart.
        workspace = workspaces.get(run.get("workspace_id", ""))
        run["workspace_name"] = workspace.name if workspace else ""
        run["workspace_slug"] = workspace.slug if workspace else ""


def _attention_payload(card: QueueHistoryEntry) -> dict[str, Any]:
    """One casualty card, JSON-safe.

    The websocket sends the snapshot with `json.dumps`, which has no opinion
    about `datetime` other than raising — so the stamps go out as ISO strings
    the way every other timestamp in this payload already does.
    """
    payload: dict[str, Any] = asdict(card)
    for key, value in payload.items():
        if isinstance(value, datetime):
            payload[key] = value.isoformat()
    return payload


def _build_lanes(
    session: Session,
    active_runs: list[dict[str, Any]],
    queued_runs: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Each slot with what is running in it, what is queued, and what went wrong.

    The waiting entries are the *same dicts* the queue reported, not a second
    read of the table: two reads meant a lane card and the queue total could
    disagree about what was waiting, and only one of them carried estimates.

    `attention` is the opposite half of the lane's life — entries that blocked
    or failed in it and released the slot. They used to leave the board with the
    lane, visible only in a history tab nobody has open while watching lanes, so
    a lane that had just eaten a ticket was indistinguishable from an idle one.
    """
    from loregarden.services.queue_lanes import QueueLaneService

    lanes_service = QueueLaneService(session, max_concurrent=DEFAULT_MAX_CONCURRENT)
    running_by_slot = {run.get("slot_number"): run for run in active_runs}

    waiting_by_slot: dict[int, list[dict[str, Any]]] = {}
    for entry in queued_runs:
        waiting_by_slot.setdefault(entry.get("slot_number") or 0, []).append(entry)

    attention_by_slot = QueueHistoryService(session).lane_attention()

    lanes: list[dict[str, Any]] = []
    for slot_number in lanes_service.lane_numbers():
        attention, attention_total = attention_by_slot.get(slot_number, ([], 0))
        lanes.append(
            {
                "slot_number": slot_number,
                "running": running_by_slot.get(slot_number),
                "waiting": waiting_by_slot.get(slot_number, []),
                "attention": [_attention_payload(card) for card in attention],
                "attention_total": attention_total,
            }
        )
    return lanes


def _attach_estimates(
    session: Session,
    active_runs: list[dict[str, Any]],
    queued_runs: list[dict[str, Any]],
    duration_stats: DurationStats,
    max_concurrent: int,
) -> None:
    """Price every card by what it actually has left, in place.

    A lane holds a *ticket*, and a ticket carries its unfinished children with
    it, so the honest figure for a lane card is the remaining pipeline of that
    whole subtree — not one agent's median, which is what made a feature with
    nine child tasks read as four minutes of work.

    A "stage" entry is still one run and is still priced as one.
    """
    if not duration_stats.has_history():
        for run in active_runs + queued_runs:
            run["estimated_duration_seconds"] = None
            run["estimated_remaining_seconds"] = None
            run["ticket_tree_estimate"] = None
        for entry in queued_runs:
            entry["estimated_wait_seconds"] = None
        return

    estimator = TicketTreeEstimator(session, stats=duration_stats)

    for run in active_runs:
        elapsed = float(run.get("elapsed_seconds") or 0)
        if run.get("orchestration_run_id"):
            tree = estimator.estimate(run.get("ticket_id") or "")
            remaining = tree.projected_seconds(max_concurrent)
            run["ticket_tree_estimate"] = tree.as_payload(max_concurrent)
            run["estimated_remaining_seconds"] = remaining
            # The bar needs a whole, and for a lane the whole is what has
            # already gone plus what is left. Reporting only the median of the
            # current stage drew a full bar on a ticket half done.
            run["estimated_duration_seconds"] = None if remaining is None else elapsed + remaining
        else:
            estimate = duration_stats.stage_seconds(run.get("stage_key"), run.get("agent_id"))
            run["ticket_tree_estimate"] = None
            run["estimated_duration_seconds"] = estimate
            run["estimated_remaining_seconds"] = (
                None if estimate is None else max(0.0, estimate - elapsed)
            )

    for entry in queued_runs:
        if entry.get("entry_kind") == "stage":
            estimate = duration_stats.stage_seconds(entry.get("stage_key"), entry.get("agent_id"))
            entry["ticket_tree_estimate"] = None
        else:
            tree = estimator.estimate(entry.get("ticket_id") or "")
            estimate = tree.projected_seconds(max_concurrent)
            entry["ticket_tree_estimate"] = tree.as_payload(max_concurrent)
        entry["estimated_duration_seconds"] = estimate
        entry["estimated_remaining_seconds"] = estimate

    # After the costs, because a wait is the sum of what is ahead of it in the
    # same lane — see project_lane_waits for why the lane, not the pool.
    waits = project_lane_waits(active_runs, queued_runs, duration_stats.by_agent, max_concurrent)
    for entry, wait in zip(queued_runs, waits, strict=True):
        entry["estimated_wait_seconds"] = wait


async def build_queue_status(session: Session) -> dict[str, Any]:
    """Active runs, queued runs and slot statistics for the shared queue.

    A read, and only a read. This used to repair as well — settling expired
    leases and reconciling lanes before building the snapshot — which tied the
    repair cadence to whether anyone had the dashboard open. The board polls
    every few seconds, so lanes healed continuously under observation and never
    otherwise, and the failure could only survive while nobody was looking.
    Both sweeps moved to the reconciliation timer, which runs regardless.
    """
    queue_service = ParallelQueueService(session, max_concurrent=DEFAULT_MAX_CONCURRENT)

    active_runs = await queue_service.get_active_runs()
    queued_runs = await queue_service.get_queued_runs()
    stats = queue_service.get_queue_stats()

    _label_runs(session, active_runs)
    _label_runs(session, queued_runs)

    # Empty when nothing has ever completed, which makes every estimate below
    # None. The dashboard renders that as "unknown" rather than substituting a
    # constant — see run_duration_stats. Drawn from every workspace, because
    # they all contend for the same slots.
    duration_stats = load_duration_stats(session)
    max_concurrent = stats.get("max_concurrent", DEFAULT_MAX_CONCURRENT)
    _attach_estimates(session, active_runs, queued_runs, duration_stats, max_concurrent)

    medians = duration_stats.by_agent
    waits = [
        entry["estimated_wait_seconds"]
        for entry in queued_runs
        if entry.get("estimated_wait_seconds") is not None
    ]
    return {
        "active_runs": active_runs,
        "queued_runs": queued_runs,
        # The board renders lanes, not one list: every waiting entry belongs to
        # a slot, and its position is within that slot.
        "lanes": _build_lanes(session, active_runs, queued_runs),
        "available_slots": stats.get("available_slots", 0),
        "total_slots": max_concurrent,
        "queue_length": len(queued_runs),
        "estimated_clear_seconds": project_clear_time(
            active_runs, queued_runs, medians, max_concurrent
        ),
        # The longest projected wait before something still queued starts.
        # Distinct from stats.longest_wait_seconds, which is how long the
        # oldest entry has already been waiting.
        "estimated_wait_seconds": max(waits) if waits else None,
        "stats": stats,
    }
