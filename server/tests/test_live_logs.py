import json
from datetime import timedelta

from fastapi.testclient import TestClient
from loregarden.api.tickets import _artifacts_grouped
from loregarden.models.domain import AgentRun, Artifact, RunStatus, Ticket
from loregarden.services.orchestration import OrchestrationService
from loregarden.services.seed import seed_database
from sqlmodel import Session, SQLModel, create_engine, select
from sqlmodel.pool import StaticPool


def test_start_run_bootstraps_live_log(isolated_db):
    with Session(isolated_db) as session:
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
        assert "runs" not in grouped


def test_artifacts_grouped_single_running_run_has_no_runs_key():
    """R1-AC1/AC2: exactly 1 RUNNING run -> grouped never gets a 'runs' key;
    grouped['logs']/['live'] are populated from that run's own log artifact."""
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

        active_run = AgentRun(
            run_code="run_active",
            ticket_id=ticket.id,
            workspace_id=ticket.workspace_id,
            agent_id="static_qa",
            stage_key="testing",
            status=RunStatus.RUNNING,
        )
        session.add(active_run)
        session.commit()
        session.refresh(active_run)

        session.add(
            Artifact(
                ticket_id=ticket.id,
                run_id=active_run.id,
                kind="log",
                title="Run run_active",
                content_json=json.dumps(
                    {
                        "lines": [{"time": "01:00:00", "tag": "CMD", "text": "solo"}],
                        "live": "Agent running…",
                    }
                ),
            )
        )
        session.commit()

        grouped = _artifacts_grouped(session, ticket)
        assert "runs" not in grouped
        assert grouped["logs"] == [{"time": "01:00:00", "tag": "CMD", "text": "solo"}]
        assert grouped["live"] == "Agent running…"


def test_artifacts_grouped_multiple_running_runs_has_no_runs_key():
    """R1-AC1/AC2 + R2 edge case: 2+ concurrent RUNNING runs must never populate
    grouped['runs'] (the dead branch that used to build it is gone). Also pins the
    ordering edge case: the most-recently created RUNNING run becomes `active_run`;
    when that run has no log artifact of its own yet, grouped['logs']/['live'] fall
    back to the early-return placeholder rather than reading another run's log."""
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

        run_with_log = AgentRun(
            run_code="run_with_log",
            ticket_id=ticket.id,
            workspace_id=ticket.workspace_id,
            agent_id="static_qa",
            stage_key="testing",
            status=RunStatus.RUNNING,
        )
        session.add(run_with_log)
        session.commit()
        session.refresh(run_with_log)
        session.add(
            Artifact(
                ticket_id=ticket.id,
                run_id=run_with_log.id,
                kind="log",
                title="Run run_with_log",
                content_json=json.dumps(
                    {
                        "lines": [{"time": "01:00:00", "tag": "CMD", "text": "x1"}],
                        "live": "Agent running…",
                    }
                ),
            )
        )
        session.commit()

        # Created strictly after run_with_log, so it sorts first by created_at desc
        # and becomes `active_run` — with no log artifact of its own.
        run_no_log = AgentRun(
            run_code="run_no_log",
            ticket_id=ticket.id,
            workspace_id=ticket.workspace_id,
            agent_id="static_qa",
            stage_key="testing",
            status=RunStatus.RUNNING,
            created_at=run_with_log.created_at + timedelta(seconds=5),
        )
        session.add(run_no_log)
        session.commit()
        session.refresh(run_no_log)

        grouped = _artifacts_grouped(session, ticket)

        assert "runs" not in grouped
        assert grouped["logs"] == []
        assert grouped["live"] == "Agent running…"


def test_artifacts_grouped_zero_running_runs_with_completed_run_log_has_no_runs_key():
    """AC4: no RUNNING and no AWAITING_PERMISSION runs at all (only a completed run with
    a log artifact) must not gain a 'runs' key — exercises the non-active sorted-fallback
    branch, which the other zero-run test (AWAITING_PERMISSION-only) does not reach."""
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

        done_run = AgentRun(
            run_code="run_done",
            ticket_id=ticket.id,
            workspace_id=ticket.workspace_id,
            agent_id="static_qa",
            stage_key="testing",
            status=RunStatus.SUCCEEDED,
        )
        session.add(done_run)
        session.commit()
        session.refresh(done_run)

        session.add(
            Artifact(
                ticket_id=ticket.id,
                run_id=done_run.id,
                kind="log",
                title="Run run_done",
                content_json=json.dumps(
                    {
                        "lines": [{"time": "01:00:00", "tag": "CMD", "text": "done"}],
                        "live": None,
                    }
                ),
            )
        )
        session.commit()

        grouped = _artifacts_grouped(session, ticket)
        assert "runs" not in grouped
        assert grouped["logs"] == [{"time": "01:00:00", "tag": "CMD", "text": "done"}]


def test_artifacts_grouped_no_log_artifacts_anywhere_on_ticket_has_no_runs_key():
    """2 RUNNING runs where neither has emitted a log artifact yet (log_artifacts is
    empty for the whole ticket): grouped['logs']/['live'] stay at their defaults and
    'runs' is never a key, same as every other run-count/state combination."""
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

        run_a = AgentRun(
            run_code="run_a",
            ticket_id=ticket.id,
            workspace_id=ticket.workspace_id,
            agent_id="static_qa",
            stage_key="testing",
            status=RunStatus.RUNNING,
        )
        run_b = AgentRun(
            run_code="run_b",
            ticket_id=ticket.id,
            workspace_id=ticket.workspace_id,
            agent_id="static_qa",
            stage_key="testing",
            status=RunStatus.RUNNING,
        )
        session.add_all([run_a, run_b])
        session.commit()

        grouped = _artifacts_grouped(session, ticket)

        assert "runs" not in grouped
        assert grouped["logs"] == []


def test_ticket_detail_endpoint_never_serializes_runs_key_with_concurrent_running_runs(
    client: TestClient, db_session: Session
):
    """R1-AC3: TicketDetail.artifacts is a raw ``dict[str, Any]`` (no Pydantic field
    named 'runs' to strip it), so a regression that resurrects the dead branch would
    round-trip a 'runs' key straight into the real HTTP response body. Every other
    test in this module calls `_artifacts_grouped`/`_apply_log_artifacts` directly and
    would not catch that — this one drives the actual endpoint a client hits."""
    ticket = db_session.exec(
        select(Ticket).where(Ticket.external_id == "03-wire-cli-agent-runner")
    ).first()

    run_a = AgentRun(
        run_code="run_http_a",
        ticket_id=ticket.id,
        workspace_id=ticket.workspace_id,
        agent_id="static_qa",
        stage_key="testing",
        status=RunStatus.RUNNING,
    )
    run_b = AgentRun(
        run_code="run_http_b",
        ticket_id=ticket.id,
        workspace_id=ticket.workspace_id,
        agent_id="static_qa",
        stage_key="testing",
        status=RunStatus.RUNNING,
    )
    db_session.add_all([run_a, run_b])
    db_session.commit()
    db_session.refresh(run_a)

    db_session.add(
        Artifact(
            ticket_id=ticket.id,
            run_id=run_a.id,
            kind="log",
            title="Run run_http_a",
            content_json=json.dumps(
                {
                    "lines": [{"time": "01:00:00", "tag": "CMD", "text": "http"}],
                    "live": "Agent running…",
                }
            ),
        )
    )
    db_session.commit()

    body = client.get(f"/api/tickets/{ticket.id}").json()

    assert "runs" not in body["artifacts"]
