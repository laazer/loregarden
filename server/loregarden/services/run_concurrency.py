"""Shared active-run lookups used to keep triage and stage runs mutually exclusive."""

from __future__ import annotations

from loregarden.models.domain import AgentRun, RunStatus
from sqlmodel import Session, col, select


def find_active_run(
    session: Session, ticket_id: str, *, only_agent_id: str | None = None
) -> AgentRun | None:
    """Return the first in-flight AgentRun for a ticket, if any.

    "In-flight" means RUNNING or AWAITING_PERMISSION — both hold a live CLI
    subprocess against the workspace's on-disk checkout, which is not
    worktree-isolated on the default execution path.
    """
    query = select(AgentRun).where(
        AgentRun.ticket_id == ticket_id,
        col(AgentRun.status).in_([RunStatus.RUNNING, RunStatus.AWAITING_PERMISSION]),
    )
    if only_agent_id is not None:
        query = query.where(AgentRun.agent_id == only_agent_id)
    return session.exec(query).first()
