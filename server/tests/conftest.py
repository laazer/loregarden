import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, create_engine
from sqlmodel.pool import StaticPool

from loregarden.db.session import get_session
from loregarden.main import app
from loregarden.services.seed import seed_database


@pytest.fixture(autouse=True)
def force_local_cli_adapter(monkeypatch):
    """Keep tests deterministic — do not invoke external CLIs during pytest."""
    monkeypatch.setenv("LOREGARDEN_CLI_ADAPTER", "local")
    monkeypatch.setenv("LOREGARDEN_SYNC_RUNS", "1")


@pytest.fixture(name="client")
def client_fixture(monkeypatch):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    monkeypatch.setattr("loregarden.db.session.engine", engine)
    monkeypatch.setattr("loregarden.services.run_service.engine", engine)
    monkeypatch.setattr("loregarden.services.run_log_stream.engine", engine)

    def override_session():
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_session] = override_session
    with Session(engine) as session:
        seed_database(session)
    with TestClient(app) as client:
        yield client
    app.dependency_overrides.clear()
