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
    #: The stage where it failed most, and how often; None and 0 with no failure.
    #: A tie goes to the stage named first, so the answer is stable.
    worst_stage: str | None = None
    worst_stage_failed: int = 0


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
    worst = _worst_stages(session)
    return [
        AgentRunStats(
            agent_id=agent_id,
            runs=runs or 0,
            failed=failed or 0,
            last_run_at=last,
            worst_stage=worst[agent_id][0] if agent_id in worst else None,
            worst_stage_failed=worst[agent_id][1] if agent_id in worst else 0,
        )
        for agent_id, runs, failed, last in session.exec(stmt).all()
    ]


def _worst_stages(session: Session) -> dict[str, tuple[str, int]]:
    """Each agent's most-failed stage. One more grouped scan; agents x stages rows."""
    stmt = (
        select(AgentRun.agent_id, AgentRun.stage_key, func.count())
        .where(col(AgentRun.agent_id) != "", col(AgentRun.status) == RunStatus.FAILED)
        .group_by(AgentRun.agent_id, AgentRun.stage_key)
        .order_by(AgentRun.agent_id, func.count().desc(), AgentRun.stage_key)
    )
    worst: dict[str, tuple[str, int]] = {}
    for agent_id, stage_key, count in session.exec(stmt).all():
        worst.setdefault(agent_id, (stage_key, count))
    return worst
