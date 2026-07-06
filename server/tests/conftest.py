import threading

import pytest
from fastapi.testclient import TestClient
from loregarden.db.session import get_session
from loregarden.main import app
from loregarden.services.seed import seed_database
from sqlmodel import Session, SQLModel, create_engine
from sqlmodel.pool import StaticPool


@pytest.fixture(autouse=True)
def sqlite_commit_lock(monkeypatch):
    """Serialize SQLite commits in tests — StaticPool + threads otherwise flake."""
    lock = threading.Lock()
    original_commit = Session.commit

    def locked_commit(self, *args, **kwargs):
        with lock:
            return original_commit(self, *args, **kwargs)

    monkeypatch.setattr(Session, "commit", locked_commit)


@pytest.fixture(autouse=True)
def force_local_cli_adapter(monkeypatch):
    """Keep tests deterministic — do not invoke external CLIs during pytest."""
    monkeypatch.setenv("LOREGARDEN_CLI_ADAPTER", "local")
    monkeypatch.setenv("LOREGARDEN_SYNC_RUNS", "1")
    monkeypatch.setenv("LOREGARDEN_SYNC_ORCHESTRATION", "1")


@pytest.fixture(name="client")
def client_fixture(tmp_path, monkeypatch):
    db_path = tmp_path / "pytest.db"
    engine = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    monkeypatch.setattr("loregarden.db.session.engine", engine)
    monkeypatch.setattr("loregarden.services.run_service.engine", engine)
    monkeypatch.setattr("loregarden.services.run_log_stream.engine", engine)
    monkeypatch.setattr("loregarden.services.builtin_orchestrator.engine", engine)

    def override_session():
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_session] = override_session
    with Session(engine) as session:
        seed_database(session)
    with TestClient(app) as client:
        yield client
    app.dependency_overrides.clear()


@pytest.fixture(name="db_session")
def db_session_fixture(client):
    from loregarden.db.session import engine

    with Session(engine) as session:
        yield session
