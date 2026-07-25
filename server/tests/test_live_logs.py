import json
from datetime import timedelta

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
    """AC3: exactly 1 RUNNING run -> 'runs' absent; logs/live unchanged from pre-change shape."""
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


def test_artifacts_grouped_multiple_running_runs_populates_runs_key():
    """AC1+AC2: 2+ concurrent RUNNING runs -> grouped['runs'] has one entry per run id,
    each mapped to that run's own logs/live (not another run's), and a run with no log
    artifact yet gets the placeholder shape instead of a KeyError or omission."""
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
        run_c_no_artifact = AgentRun(
            run_code="run_c",
            ticket_id=ticket.id,
            workspace_id=ticket.workspace_id,
            agent_id="static_qa",
            stage_key="testing",
            status=RunStatus.RUNNING,
        )
        session.add(run_a)
        session.add(run_b)
        session.add(run_c_no_artifact)
        session.commit()
        session.refresh(run_a)
        session.refresh(run_b)
        session.refresh(run_c_no_artifact)

        session.add(
            Artifact(
                ticket_id=ticket.id,
                run_id=run_a.id,
                kind="log",
                title="Run run_a",
                content_json=json.dumps(
                    {
                        "lines": [
                            {"time": "01:00:00", "tag": "CMD", "text": "a1"},
                            {"time": "01:00:01", "tag": "CMD", "text": "a2"},
                        ],
                        "live": "Agent running…",
                    }
                ),
            )
        )
        session.add(
            Artifact(
                ticket_id=ticket.id,
                run_id=run_b.id,
                kind="log",
                title="Run run_b",
                content_json=json.dumps(
                    {
                        "lines": [{"time": "02:00:00", "tag": "CMD", "text": "b1"}],
                        "live": "Agent running…",
                    }
                ),
            )
        )
        session.commit()

        grouped = _artifacts_grouped(session, ticket)

        assert "runs" in grouped
        assert set(grouped["runs"].keys()) == {run_a.id, run_b.id, run_c_no_artifact.id}

        assert grouped["runs"][run_a.id]["logs"] == [
            {"time": "01:00:00", "tag": "CMD", "text": "a1"},
            {"time": "01:00:01", "tag": "CMD", "text": "a2"},
        ]
        assert grouped["runs"][run_b.id]["logs"] == [
            {"time": "02:00:00", "tag": "CMD", "text": "b1"}
        ]
        assert grouped["runs"][run_c_no_artifact.id] == {
            "logs": [],
            "live": "Agent running…",
        }

        # The single-representative grouped['logs']/grouped['live'] selection is untouched:
        # run_c_no_artifact is the most-recently created RUNNING run, so it is
        # `active_run`, has no log artifact, and the pre-existing early-return
        # placeholder still applies at the top level (see
        # test_artifacts_grouped_no_artifact_run_is_most_recent_still_populates_runs,
        # which pins this exact selection behavior with a deterministic timestamp).
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


def test_artifacts_grouped_no_artifact_run_is_most_recent_still_populates_runs():
    """Targets the plan's ordering risk directly: the artifact-less run is the most
    recent RUNNING run, so it is `active_run` and triggers the early-return branch.
    grouped['runs'] must still contain every RUNNING run, not just the ones seen
    before the early return."""
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

        assert "runs" in grouped
        assert set(grouped["runs"].keys()) == {run_with_log.id, run_no_log.id}
        assert grouped["runs"][run_no_log.id] == {"logs": [], "live": "Agent running…"}
        assert grouped["runs"][run_with_log.id]["logs"] == [
            {"time": "01:00:00", "tag": "CMD", "text": "x1"}
        ]
        # Top-level selection still reflects the early-return placeholder for the
        # (most-recent, artifact-less) active run.
        assert grouped["logs"] == []
        assert grouped["live"] == "Agent running…"


def test_artifacts_grouped_awaiting_permission_run_excluded_from_runs_key():
    """AC constraint: AWAITING_PERMISSION runs must never appear in grouped['runs'],
    even when RUNNING runs are present on the same ticket at the same time."""
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
        awaiting_run = AgentRun(
            run_code="run_awaiting",
            ticket_id=ticket.id,
            workspace_id=ticket.workspace_id,
            agent_id="static_qa",
            stage_key="testing",
            status=RunStatus.AWAITING_PERMISSION,
        )
        session.add_all([run_a, run_b, awaiting_run])
        session.commit()
        for run in (run_a, run_b, awaiting_run):
            session.refresh(run)

        for run, text in ((run_a, "a1"), (run_b, "b1"), (awaiting_run, "await1")):
            session.add(
                Artifact(
                    ticket_id=ticket.id,
                    run_id=run.id,
                    kind="log",
                    title=f"Run {run.run_code}",
                    content_json=json.dumps(
                        {
                            "lines": [{"time": "01:00:00", "tag": "CMD", "text": text}],
                            "live": "Agent running…",
                        }
                    ),
                )
            )
        session.commit()

        grouped = _artifacts_grouped(session, ticket)

        assert "runs" in grouped
        assert set(grouped["runs"].keys()) == {run_a.id, run_b.id}
        assert awaiting_run.id not in grouped["runs"]


def test_artifacts_grouped_running_run_empty_live_string_falls_back_to_placeholder():
    """A RUNNING run's log artifact with an explicit empty-string 'live' (not missing,
    not None) must still fall back to the 'Agent running…' placeholder in its
    grouped['runs'] entry, not surface the empty string verbatim."""
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
        session.refresh(run_a)
        session.refresh(run_b)

        session.add(
            Artifact(
                ticket_id=ticket.id,
                run_id=run_a.id,
                kind="log",
                title="Run run_a",
                content_json=json.dumps({"lines": [], "live": ""}),
            )
        )
        session.add(
            Artifact(
                ticket_id=ticket.id,
                run_id=run_b.id,
                kind="log",
                title="Run run_b",
                content_json=json.dumps(
                    {"lines": [{"time": "01:00:00", "tag": "CMD", "text": "b1"}], "live": None}
                ),
            )
        )
        session.commit()

        grouped = _artifacts_grouped(session, ticket)

        assert grouped["runs"][run_a.id]["live"] == "Agent running…"
        assert grouped["runs"][run_b.id]["live"] == "Agent running…"


def test_artifacts_grouped_running_runs_with_no_log_artifacts_anywhere_on_ticket():
    """Adversarial gap: 2 RUNNING runs where NEITHER has emitted a log artifact yet
    (log_artifacts is empty for the whole ticket). _artifacts_grouped only calls
    _apply_log_artifacts when `log_artifacts` is truthy, so this path currently
    never builds grouped['runs'] even though 2+ runs are concurrently RUNNING —
    surfacing a real blind spot in the spec's "2+ RUNNING" framing, which assumed
    at least one artifact exists."""
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

        # Documents current (pre- and post-fix) behavior: no log artifacts anywhere
        # on the ticket means _apply_log_artifacts is never invoked, so 'runs' stays
        # absent even with 2 concurrent RUNNING runs. If a future change makes
        # grouped['runs'] unconditional on log_artifacts existing, update this
        # assertion rather than treating its failure as a regression.
        assert "runs" not in grouped
        assert grouped["logs"] == []
