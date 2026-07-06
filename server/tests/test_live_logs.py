import json

from loregarden.api.tickets import _artifacts_grouped
from loregarden.models.domain import AgentRun, Artifact, RunStatus, Ticket
from loregarden.services.orchestration import OrchestrationService
from loregarden.services.seed import seed_database
from sqlmodel import Session, SQLModel, create_engine, select
from sqlmodel.pool import StaticPool


def test_start_run_bootstraps_live_log():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)

    import loregarden.services.run_log_stream as stream_mod

    original_engine = stream_mod.engine
    stream_mod.engine = engine
    try:
        with Session(engine) as session:
            seed_database(session)
            ticket = session.exec(
                select(Ticket).where(Ticket.external_id == "03-wire-cli-agent-runner")
            ).first()
            orch = OrchestrationService(session)
            run = orch.start_run(ticket, stage_key="planning")
            artifact = session.exec(
                select(Artifact).where(Artifact.run_id == run.id, Artifact.kind == "log")
            ).first()
            assert artifact is not None
            content = json.loads(artifact.content_json)
            assert content["live"] == "Agent running…"
            assert any(line["tag"] == "RUN" for line in content["lines"])
    finally:
        stream_mod.engine = original_engine


def test_artifacts_grouped_prefers_active_run_without_stale_fallback():
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

        stale_run = AgentRun(
            run_code="run_stale",
            ticket_id=ticket.id,
            workspace_id=ticket.workspace_id,
            agent_id="static_qa",
            stage_key="testing",
            status=RunStatus.FAILED,
        )
        active_run = AgentRun(
            run_code="run_active",
            ticket_id=ticket.id,
            workspace_id=ticket.workspace_id,
            agent_id="static_qa",
            stage_key="testing",
            status=RunStatus.AWAITING_PERMISSION,
        )
        session.add(stale_run)
        session.add(active_run)
        session.commit()
        session.refresh(stale_run)
        session.refresh(active_run)

        session.add(
            Artifact(
                ticket_id=ticket.id,
                run_id=stale_run.id,
                kind="log",
                title="Run run_stale",
                content_json=json.dumps(
                    {
                        "lines": [{"time": "01:00:00", "tag": "CMD", "text": "stale"}],
                        "live": "Agent running…",
                    }
                ),
            )
        )
        session.commit()

        grouped = _artifacts_grouped(session, ticket)
        assert grouped["logs"] == []
        assert "Awaiting your approval" in grouped["live"]
