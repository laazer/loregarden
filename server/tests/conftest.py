import subprocess

import pytest
from fastapi.testclient import TestClient
from loregarden.config import settings
from loregarden.db.session import get_session
from loregarden.main import app
from loregarden.models.domain import Workspace
from loregarden.services.git_subprocess import GIT_LOCATION_ENV_VARS
from loregarden.services.seed import seed_database
from sqlmodel import Session, SQLModel, create_engine, select

# Every module that binds the DB engine at import time via
# `from loregarden.db.session import engine`. The isolated_db fixture redirects
# all of them to the per-test engine; missing one lets that code path hit the
# real (unschema'd) database in a fresh checkout — "no such table" errors.
_ENGINE_BINDINGS = (
    "loregarden.db.session.engine",
    "loregarden.main.engine",
    "loregarden.services.run_service.engine",
    "loregarden.services.run_log_stream.engine",
    "loregarden.services.builtin_orchestrator.engine",
    "loregarden.agents.executors.permission_bridge.engine",
    "loregarden.services.triage_run_service.engine",
    "loregarden.services.branch_triage_run_service.engine",
)


@pytest.fixture(autouse=True)
def scrub_ambient_git_env(monkeypatch):
    """Keep this suite's own git calls resolving through `cwd`.

    Most tests build a throwaway repo in `tmp_path` and shell out to git
    directly. An ambient GIT_DIR — which git exports into any hook this suite
    runs under, e.g. pre-push from a worktree — overrides `cwd` and points those
    calls at the loregarden repo instead. Scrubbing it here makes the suite
    hermetic regardless of how it was invoked.

    This does not mask the service-layer scrub: test_git_subprocess.py sets
    GIT_DIR explicitly inside its own tests to prove `run_git` handles it.
    """
    for name in GIT_LOCATION_ENV_VARS:
        monkeypatch.delenv(name, raising=False)


@pytest.fixture(autouse=True)
def force_local_cli_adapter(monkeypatch):
    """Keep tests deterministic — do not invoke external CLIs during pytest."""
    monkeypatch.setenv("LOREGARDEN_CLI_ADAPTER", "local")
    monkeypatch.setenv("LOREGARDEN_SYNC_RUNS", "1")
    monkeypatch.setenv("LOREGARDEN_SYNC_ORCHESTRATION", "1")


@pytest.fixture(name="isolated_db", autouse=True)
def isolated_db_fixture(tmp_path, monkeypatch):
    """Give every test an isolated, schema'd SQLite engine and point all
    module-global engine bindings at it.

    This makes DB-backed code work whether it is reached through the API or
    called directly (a service that spawns a run recorder on the global engine
    now shares the test engine). It does NOT seed — request the ``client``
    fixture, or call ``seed_database(session)``, when a test needs the built-in
    workspace/ticket/agent data.
    """
    engine = create_engine(
        f"sqlite:///{tmp_path / 'pytest.db'}",
        connect_args={"check_same_thread": False, "timeout": 30},
    )
    # A real pool, not StaticPool: StaticPool hands every thread the *same* DBAPI
    # connection, so concurrent sessions share one transaction. One session's
    # commit ends another's mid-flight transaction — the second then dies with
    # "cannot commit - no transaction is active", or reads a row a peer already
    # rolled back (ObjectDeletedError). WAL lets those per-thread connections
    # write concurrently instead of serialising on the whole file.
    with engine.connect() as conn:
        conn.exec_driver_sql("PRAGMA journal_mode=WAL")
    SQLModel.metadata.create_all(engine)
    for target in _ENGINE_BINDINGS:
        monkeypatch.setattr(target, engine)
    return engine


def _isolate_seeded_workspace_repo(session: Session, tmp_path) -> None:
    """Repoint the seeded "loregarden" workspace at a throwaway git repo.

    Orchestration/CLI-executor code paths run real `git checkout -B` against
    a workspace's resolved repo root. Left pointed at repo_path="." (which
    resolves against the real settings.repo_root), tests that orchestrate the
    seeded workspace's tickets would check out branches in the actual project
    working directory. Profile/doc loading falls back to settings.repo_root
    directly and is unaffected by this repo_path change.
    """
    repo = tmp_path / "loregarden-seeded-repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-b", "main"], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"], cwd=repo, check=True, capture_output=True
    )
    (repo / "README.md").write_text("# test\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=repo, check=True, capture_output=True)

    ws = session.exec(select(Workspace).where(Workspace.slug == "loregarden")).first()
    if ws:
        ws.repo_path = str(repo)
        session.add(ws)
        session.commit()


@pytest.fixture(name="client")
def client_fixture(isolated_db, tmp_path):
    def override_session():
        with Session(isolated_db) as session:
            yield session

    app.dependency_overrides[get_session] = override_session
    with Session(isolated_db) as session:
        seed_database(session)
        _isolate_seeded_workspace_repo(session, tmp_path)
    with TestClient(app) as client:
        yield client
    app.dependency_overrides.clear()


@pytest.fixture(name="db_session")
def db_session_fixture(client, isolated_db):
    with Session(isolated_db) as session:
        yield session


@pytest.fixture(name="git_repo")
def git_repo_fixture(tmp_path):
    """A throwaway git repo with one commit, for tests that assert on staging."""
    root = tmp_path / "repo"
    root.mkdir()

    def git(*args):
        subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)

    git("init", "-q")
    git("config", "user.email", "t@example.com")
    git("config", "user.name", "Test")
    (root / "seed.txt").write_text("seed\n")
    git("add", "-A")
    git("commit", "-q", "-m", "seed")
    return root


@pytest.fixture(autouse=True)
def isolated_memory_store(tmp_path_factory, monkeypatch):
    """Keep the suite out of the real Obsidian vault.

    isolated_db redirects the ticket database but not the memory store, so the
    memory tools and inherited-wisdom read and write the developer's actual
    iCloud vault while tests run. That makes those tests depend on iCloud being
    materialised and on per-binary macOS privacy grants rather than on the code
    — they fail with "unable to open database file" when it is not.

    Allocated outside the test's own tmp_path: tests that list tmp_path would
    otherwise see this directory, and one that asserts on its contents did.
    """
    root = tmp_path_factory.mktemp("memory_store")
    vault = root / "vault"
    vault.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(settings, "obsidian_vault_dir", str(vault), raising=False)
    monkeypatch.setattr(
        settings, "memory_sqlite_url", f"sqlite:///{root / 'memory.db'}", raising=False
    )
    monkeypatch.setattr(settings, "icloud_root", "", raising=False)
