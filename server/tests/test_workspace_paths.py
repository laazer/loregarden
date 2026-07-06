import os
import subprocess
import sys
from pathlib import Path

from sqlmodel.pool import StaticPool


def test_sqlite_db_path_resolves_relative_to_repo_root(monkeypatch, tmp_path):
    monkeypatch.setenv("LOREGARDEN_REPO_ROOT", str(tmp_path))
    monkeypatch.setenv("LOREGARDEN_DATABASE_URL", "sqlite:///data/loregarden.db")

    from loregarden.cli.init_db import sqlite_db_path

    assert sqlite_db_path() == tmp_path / "data" / "loregarden.db"


def test_resolve_workspace_root_relative(tmp_path, monkeypatch):
    monkeypatch.setattr("loregarden.services.workspace_paths.settings.repo_root", tmp_path)
    project = tmp_path / "sample-project"
    project.mkdir()
    from loregarden.models.domain import Workspace
    from loregarden.services.workspace_paths import resolve_workspace_root

    ws = Workspace(slug="sample", name="Sample", repo_path="sample-project")
    assert resolve_workspace_root(ws) == project.resolve()


def test_resolve_workspace_root_absolute(tmp_path):
    from loregarden.models.domain import Workspace
    from loregarden.services.workspace_paths import (
        resolve_agent_context_dir,
        resolve_project_board_dir,
        resolve_workspace_root,
        workspace_repo_exists,
    )

    ws = Workspace(slug="sample", name="Sample", repo_path=str(tmp_path))
    assert resolve_workspace_root(ws) == tmp_path.resolve()
    assert resolve_agent_context_dir(ws) == tmp_path / "agent_context"
    assert resolve_project_board_dir(ws) == tmp_path / "project_board"
    assert workspace_repo_exists(ws) is True


def test_workspace_repo_exists_missing(tmp_path):
    from loregarden.models.domain import Workspace
    from loregarden.services.workspace_paths import workspace_repo_exists

    missing = Workspace(slug="y", name="Y", repo_path=str(tmp_path / "nope"))
    assert workspace_repo_exists(missing) is False


def test_create_workspace_exposes_repo_metadata(client):
    res = client.post(
        "/api/workspaces",
        json={
            "slug": "sample-ref",
            "name": "Sample Ref",
            "workflow_template_slug": "extended-tdd",
            "repo_path": ".",
        },
    )
    assert res.status_code == 201

    workspaces = client.get("/api/workspaces").json()
    sample = next(w for w in workspaces if w["slug"] == "sample-ref")
    assert sample["workflow_template_slug"] == "extended-tdd"
    assert sample["repo_path"] == "."
    assert "repo_root" in sample
    assert "repo_exists" in sample


def test_create_workspace_with_orchestration_profile(client, db_session):
    res = client.post(
        "/api/workspaces",
        json={
            "slug": "blobert-ref",
            "name": "Blobert Ref",
            "workflow_template_slug": "blobert-tdd",
            "repo_path": ".",
            "orchestration_profile_slug": "blobert",
        },
    )
    assert res.status_code == 201

    from loregarden.models.domain import Workspace
    from sqlmodel import select

    row = db_session.exec(select(Workspace).where(Workspace.slug == "blobert-ref")).first()
    assert row is not None
    assert row.orchestration_profile_slug == "blobert"


def test_cli_executor_fails_when_workspace_repo_missing(tmp_path):
    from loregarden.agents.executors.cli import CliAgentExecutor
    from loregarden.models.domain import AgentRun, RunStatus, Ticket, Workspace
    from loregarden.services.seed import seed_database
    from sqlmodel import Session, SQLModel, create_engine

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        seed_database(session)
        ws = Workspace(
            slug="ghost",
            name="Ghost",
            repo_path=str(tmp_path / "missing"),
        )
        session.add(ws)
        session.commit()
        session.refresh(ws)

        ticket = Ticket(
            external_id="ghost-task",
            workspace_id=ws.id,
            title="Ghost task",
            description="",
        )
        session.add(ticket)
        session.commit()
        session.refresh(ticket)

        run = AgentRun(
            run_code="run_ghost",
            ticket_id=ticket.id,
            workspace_id=ws.id,
            agent_id="planner",
            stage_key="planning",
            status=RunStatus.RUNNING,
        )
        session.add(run)
        session.commit()

        completed = CliAgentExecutor(session).execute(run, ticket)
        assert completed.status == RunStatus.FAILED
        assert "does not exist" in (completed.stderr or "")


def test_init_db_cli_creates_seeded_database(tmp_path, monkeypatch):
    db_path = tmp_path / "fresh.db"
    monkeypatch.setenv("LOREGARDEN_REPO_ROOT", str(tmp_path))
    monkeypatch.setenv("LOREGARDEN_DATABASE_URL", f"sqlite:///{db_path}")

    server_dir = Path(__file__).resolve().parents[1]
    env = os.environ.copy()
    proc = subprocess.run(
        [sys.executable, "-m", "loregarden.cli.init_db"],
        cwd=str(server_dir),
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    assert db_path.is_file()
    assert "seeded bootstrap data" in proc.stdout

    from loregarden.models.domain import Ticket, Workspace
    from sqlmodel import Session, create_engine, select

    engine = create_engine(f"sqlite:///{db_path}")
    with Session(engine) as session:
        workspaces = session.exec(select(Workspace)).all()
        tickets = session.exec(select(Ticket)).all()
    assert {ws.slug for ws in workspaces} == {"loregarden"}
    assert len(tickets) >= 5


def test_init_db_empty_skips_seed(tmp_path, monkeypatch):
    db_path = tmp_path / "empty.db"
    monkeypatch.setenv("LOREGARDEN_REPO_ROOT", str(tmp_path))
    monkeypatch.setenv("LOREGARDEN_DATABASE_URL", f"sqlite:///{db_path}")

    server_dir = Path(__file__).resolve().parents[1]
    proc = subprocess.run(
        [sys.executable, "-m", "loregarden.cli.init_db", "--empty"],
        cwd=str(server_dir),
        env=os.environ.copy(),
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    assert "schema only" in proc.stdout

    from loregarden.models.domain import Ticket
    from sqlmodel import Session, create_engine, select

    engine = create_engine(f"sqlite:///{db_path}")
    with Session(engine) as session:
        assert session.exec(select(Ticket)).first() is None
