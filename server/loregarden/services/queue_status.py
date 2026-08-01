"""The parallel-execution snapshot, in one place.

Both the REST status endpoint and the queue websocket answer the same
question, and a client that switches between them must not see a different
shape depending on which one replied — so neither builds the payload itself.
"""

from __future__ import annotations

from typing import Any

from loregarden.models.domain import StudioAgent, Ticket
from loregarden.services.parallel_queue import ParallelQueueService
from loregarden.services.run_duration_stats import (
    estimate_for,
    median_duration_by_agent,
    project_clear_time,
)
from sqlmodel import Session, select

#: Slots per workspace. Matches the default the REST endpoint has always used.
DEFAULT_MAX_CONCURRENT = 3


def _label_runs(session: Session, runs: list[dict[str, Any]]) -> None:
    """Attach the names behind the ids, in place.

    The queue tables carry ids because that is what scheduling needs; a slot
    card showing a bare uuid is not something anyone can read. Two batched
    selects for the whole snapshot — a lookup per run would add an N+1 on the
    path the websocket walks every five seconds.
    """
    ticket_ids = {run["ticket_id"] for run in runs if run.get("ticket_id")}
    agent_slugs = {run["agent_id"] for run in runs if run.get("agent_id")}

    tickets: dict[str, Ticket] = {}
    if ticket_ids:
        rows = session.exec(select(Ticket).where(Ticket.id.in_(ticket_ids))).all()
        tickets = {ticket.id: ticket for ticket in rows}

    agents: dict[str, StudioAgent] = {}
    if agent_slugs:
        rows = session.exec(select(StudioAgent).where(StudioAgent.slug.in_(agent_slugs))).all()
        agents = {agent.slug: agent for agent in rows}

    for run in runs:
        ticket = tickets.get(run.get("ticket_id", ""))
        run["ticket_title"] = ticket.title if ticket else ""
        run["ticket_code"] = ticket.external_id if ticket else ""
        run["ticket_state"] = ticket.state.value if ticket else ""

        agent = agents.get(run.get("agent_id", ""))
        # Falling back to the slug is not a placeholder: a run whose agent
        # definition was deleted still has a meaningful slug to show.
        run["agent_name"] = agent.name if agent else run.get("agent_id", "")


async def build_queue_status(session: Session, workspace_id: str) -> dict[str, Any]:
    """Active runs, queued runs and slot statistics for one workspace."""
    queue_service = ParallelQueueService(session, max_concurrent=DEFAULT_MAX_CONCURRENT)

    active_runs = await queue_service.get_active_runs(workspace_id)
    queued_runs = await queue_service.get_queued_runs(workspace_id)
    stats = queue_service.get_queue_stats(workspace_id)

    _label_runs(session, active_runs)
    _label_runs(session, queued_runs)

    # Empty when the workspace has no completed runs to learn from, which makes
    # every estimate below None. The dashboard renders that as "unknown" rather
    # than substituting a constant — see run_duration_stats.
    medians = median_duration_by_agent(session, workspace_id)
    max_concurrent = stats.get("max_concurrent", DEFAULT_MAX_CONCURRENT)
    for run in active_runs:
        run["estimated_duration_seconds"] = estimate_for(medians, run.get("agent_id"))

    return {
        "active_runs": active_runs,
        "queued_runs": queued_runs,
        "available_slots": stats.get("available_slots", 0),
        "total_slots": max_concurrent,
        "queue_length": len(queued_runs),
        "estimated_clear_seconds": project_clear_time(
            active_runs, queued_runs, medians, max_concurrent
        ),
        "stats": stats,
    }
