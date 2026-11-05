"""One agent's run record, for the agent's page in Agent Studio.

The index ranks agents by failure rate; this answers the next question, where
and why one agent fails: its finished runs per stage, each stage's failures by
who could have unblocked them (`BlockKind`), and its latest runs with the
ticket each ran on. Two grouped or limited queries, neither touching `stdout`/`stderr`, which
`agent_runs` carries inline.
"""

from collections import Counter, defaultdict
from datetime import datetime

from loregarden.models.domain import AgentRun, BlockKind, RunStatus, Ticket
from loregarden.services.agent_run_stats import FINISHED
from pydantic import BaseModel
from sqlalchemy import func
from sqlmodel import Session, col, select

RECENT_RUNS = 12


class KindCount(BaseModel):
    #: None for a failure the control plane never classified.
    kind: BlockKind | None
    count: int


class StageRecord(BaseModel):
    stage_key: str
    #: Finished runs at this stage: succeeded, failed or cancelled.
    runs: int
    failed: int
    #: `failed` split by who could have unblocked it, largest first; the counts
    #: always sum to `failed`.
    failed_by_kind: list[KindCount]


class RecentRun(BaseModel):
    id: str
    run_code: str
    status: RunStatus
    stage_key: str
    failure_kind: BlockKind | None
    created_at: datetime
    finished_at: datetime | None
    #: None when the run belongs to no ticket (a chat or a repair outside one).
    ticket_external_id: str | None
    ticket_title: str | None


class AgentRunRecord(BaseModel):
    agent_id: str
    #: Most failures first, then most runs.
    stages: list[StageRecord]
    #: Newest first, at most `RECENT_RUNS`, any status.
    recent: list[RecentRun]


def _stages(session: Session, agent_id: str) -> list[StageRecord]:
    stmt = (
        select(AgentRun.stage_key, AgentRun.status, AgentRun.failure_kind, func.count())
        .where(AgentRun.agent_id == agent_id, col(AgentRun.status).in_(FINISHED))
        .group_by(AgentRun.stage_key, AgentRun.status, AgentRun.failure_kind)
    )
    runs: Counter[str] = Counter()
    failures: dict[str, Counter[BlockKind | None]] = defaultdict(Counter)
    for stage_key, status, kind, count in session.exec(stmt).all():
        runs[stage_key] += count
        if status == RunStatus.FAILED:
            failures[stage_key][kind] += count
    rows = [
        StageRecord(
            stage_key=stage_key,
            runs=total,
            failed=failures[stage_key].total(),
            failed_by_kind=[
                KindCount(kind=kind, count=count)
                for kind, count in failures[stage_key].most_common()
            ],
        )
        for stage_key, total in runs.items()
    ]
    return sorted(rows, key=lambda row: (-row.failed, -row.runs, row.stage_key))


def _recent(session: Session, agent_id: str) -> list[RecentRun]:
    stmt = (
        select(AgentRun, col(Ticket.external_id), col(Ticket.title))
        .join(Ticket, col(Ticket.id) == col(AgentRun.ticket_id), isouter=True)
        .where(AgentRun.agent_id == agent_id)
        .order_by(col(AgentRun.created_at).desc())
        .limit(RECENT_RUNS)
    )
    return [
        RecentRun(
            id=run.id,
            run_code=run.run_code,
            status=run.status,
            stage_key=run.stage_key,
            failure_kind=run.failure_kind,
            created_at=run.created_at,
            finished_at=run.finished_at,
            ticket_external_id=external_id,
            ticket_title=title,
        )
        for run, external_id, title in session.exec(stmt).all()
    ]


def agent_run_record(session: Session, agent_id: str) -> AgentRunRecord:
    """An agent that never ran gets empty lists, not an error: its page still renders."""
    return AgentRunRecord(
        agent_id=agent_id,
        stages=_stages(session, agent_id),
        recent=_recent(session, agent_id),
    )
