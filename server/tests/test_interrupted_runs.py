from loregarden.models.domain import AgentRun, RunStatus, StageStatus, Ticket
from loregarden.services.orchestration import OrchestrationService
from loregarden.services.run_service import (
    INTERRUPTED_RUN_MESSAGE,
    RunService,
    fail_interrupted_runs,
)
from loregarden.services.seed import seed_database
from sqlmodel import Session, SQLModel, create_engine, select
from sqlmodel.pool import StaticPool


def test_fail_interrupted_runs_marks_orphans_failed():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)

    with Session(engine) as session:
        seed_database(session)
        ticket = session.exec(
            select(Ticket).where(Ticket.external_id == "03-wire-cli-agent-runner")
        ).first()
        orch = OrchestrationService(session)
        stuck = orch.start_run(ticket, stage_key="testing")
        stuck.status = RunStatus.RUNNING
        session.add(stuck)
        session.commit()

        failed = fail_interrupted_runs(session, ticket_id=ticket.id, stage_key="testing")
        assert len(failed) == 1
        assert failed[0].id == stuck.id
        session.refresh(stuck)
        assert stuck.status == RunStatus.FAILED
        assert INTERRUPTED_RUN_MESSAGE in stuck.stderr
        session.refresh(ticket)
        assert ticket.workflow_stage_status == StageStatus.BLOCKED


def test_start_run_async_fails_prior_running_run():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)

    with Session(engine) as session:
        seed_database(session)
        ticket = session.exec(
            select(Ticket).where(Ticket.external_id == "03-wire-cli-agent-runner")
        ).first()
        run_svc = RunService(session)
        first = run_svc.start_run_async(ticket, stage_key="testing")
        assert first.status == RunStatus.RUNNING

        second = run_svc.start_run_async(ticket, stage_key="testing")
        session.refresh(first)
        assert first.status == RunStatus.FAILED
        assert second.status == RunStatus.RUNNING
        assert second.id != first.id

        runs = session.exec(
            select(AgentRun).where(
                AgentRun.ticket_id == ticket.id,
                AgentRun.stage_key == "testing",
            )
        ).all()
        running = [run for run in runs if run.status == RunStatus.RUNNING]
        assert len(running) == 1
        assert running[0].id == second.id
