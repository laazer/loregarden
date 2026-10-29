"""How each agent has fared across its runs: Agent Studio's index.

One grouped query over `agent_runs`, so the cost is one scan however many
agents there are. Only finished runs count toward the rate: a run still going
has not failed yet, and counting it as a success would flatter an agent that
is mid-way through a bad stretch.
"""

from datetime import datetime

from loregarden.models.domain import AgentRun, RunStatus
from pydantic import BaseModel
from sqlalchemy import case, func
from sqlmodel import Session, col, select

FINISHED = (RunStatus.SUCCEEDED, RunStatus.FAILED, RunStatus.CANCELLED)


class AgentRunStats(BaseModel):
    agent_id: str
    #: Finished runs: succeeded, failed or cancelled.
    runs: int
    failed: int
    #: When its latest run of any status was created (naive UTC, as stored).
    last_run_at: datetime | None


def agent_run_stats(session: Session) -> list[AgentRunStats]:
    """Every agent id that has run, finished or not. Runs with no agent id are left out."""
    finished = col(AgentRun.status).in_(FINISHED)
    stmt = (
        select(
            AgentRun.agent_id,
            func.sum(case((finished, 1), else_=0)),
            func.sum(case((col(AgentRun.status) == RunStatus.FAILED, 1), else_=0)),
            func.max(AgentRun.created_at),
        )
        .where(col(AgentRun.agent_id) != "")
        .group_by(AgentRun.agent_id)
    )
    return [
        AgentRunStats(agent_id=agent_id, runs=runs or 0, failed=failed or 0, last_run_at=last)
        for agent_id, runs, failed, last in session.exec(stmt).all()
    ]
