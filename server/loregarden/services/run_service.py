from sqlmodel import Session, select

from loregarden.agents.executors.cli import CliAgentExecutor
from loregarden.models.domain import AgentRun, Ticket
from loregarden.services.orchestration import OrchestrationService


class RunService:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.orchestration = OrchestrationService(session)
        self.executor = CliAgentExecutor(session)

    def start_and_execute(
        self, ticket: Ticket, *, stage_key: str | None = None
    ) -> tuple[AgentRun, Ticket]:
        run = self.orchestration.start_run(ticket, stage_key=stage_key)
        self.session.refresh(ticket)
        completed_run = self.executor.execute(run, ticket)
        self.session.refresh(ticket)
        return completed_run, ticket

    def list_runs(
        self,
        *,
        ticket_id: str | None = None,
        limit: int = 50,
    ) -> list[AgentRun]:
        query = select(AgentRun).order_by(AgentRun.created_at.desc()).limit(limit)
        if ticket_id:
            query = query.where(AgentRun.ticket_id == ticket_id)
        return list(self.session.exec(query).all())

    def get_run(self, run_id: str) -> AgentRun | None:
        return self.session.get(AgentRun, run_id)
