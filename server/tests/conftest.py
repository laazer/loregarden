import os
import subprocess
from contextlib import ExitStack
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from lore_eden.testing import pytest_profile
from loregarden.config import settings
from loregarden.db.session import get_session, init_db
from loregarden.main import app
from loregarden.models.domain import Workspace
from loregarden.services import (
    codex_discovery,
    docker_capacity,
    local_instances,
    opencode_discovery,
    reference_cache,
)
from loregarden.services.cli_settings import ADAPTER_BINARIES
from loregarden.services.git_subprocess import GH_BINARY_ENV, GIT_LOCATION_ENV_VARS
from loregarden.services.memory_store import MemoryGraphStore, ObsidianMemoryStore
from loregarden.services.seed import seed_database
from sqlmodel import Session, create_engine, select
from tests.db_templates import SeededTemplate, build_schema_template, copy_database
from tests.memory_guard import forbidden_memory_roots, reject_if_forbidden
from tests.repo_templates import from_template, set_template_root
from tests.worktree_helpers import seed_stage_report_contract


def pytest_configure(config):
    """Profile this run when `LORE_EDEN_PROFILE` names an output file; otherwise do nothing.

    `task test:profile` sets it. The plugin records per-test setup/call/teardown
    seconds and prints the slowest; under xdist only the controller writes.
    """
    pytest_profile.register(config, suite="server")


# Every module that binds the DB engine at import time via
# `from loregarden.db.session import engine`. The isolated_db fixture redirects
# all of them to the per-test engine; missing one lets that code path hit the
# real (unschema'd) database in a fresh checkout — "no such table" errors.
_ENGINE_BINDINGS = (
    "loregarden.db.session.engine",
    "loregarden.main.engine",
    "loregarden.services.run_service.engine",
    "loregarden.services.run_log_stream.engine",
    "loregarden.services.chat_thinking.engine",
    "loregarden.api.chat_turn_events.engine",
    "loregarden.services.builtin_orchestrator.engine",
    "loregarden.services.stage_fanout_service.engine",
    "loregarden.services.drain.engine",
    "loregarden.services.process_identity.engine",
    "loregarden.services.run_cancellation.engine",
    "loregarden.services.run_lease.engine",
    "loregarden.agents.executors.permission_bridge.engine",
    "loregarden.services.triage_run_service.engine",
    "loregarden.services.branch_triage_run_service.engine",
    "loregarden.services.baxter_chat_run_service.engine",
    "loregarden.services.ticket_studio_run_service.engine",
    "loregarden.services.btw_run_service.engine",
    "loregarden.services.github_sync_scheduler.engine",
    "loregarden.services.github_push_on_edit.engine",
)


@pytest.fixture(autouse=True, scope="session")
def _no_shutdown_drain_in_tests():
    """Tests do not wait 20s to shut down an app they built.

    The lifespan drains on the way out, and a test suite builds and tears down
    many apps while leaving RUNNING rows behind on purpose — so each teardown
    sat through the full production window waiting for runs that no process was
    ever going to finish. One test took 56 seconds; the suite took 22 minutes.

    Zero is the documented off switch, and it exercises the same code path: the
    drain still begins, still refuses new work, and still hands what is left to
    the interruption path. Only the wait is skipped. A test that wants the wait
    passes its own timeout to `wait_for_quiescence`.
    """
    from loregarden.config import settings

    previous = settings.drain_timeout_seconds
    settings.drain_timeout_seconds = 0
    yield
    settings.drain_timeout_seconds = previous


@pytest.fixture(autouse=True, scope="session")
def _no_github_push_worker_in_tests():
    """No background push-on-edit worker in apps the suite builds.

    Every app lifespan would otherwise start one, and it would drain edits from
    whichever test happened to be running on a thread nobody awaits. The push
    tests drive `process_due_pushes` themselves.
    """
    from loregarden.config import settings

    previous = settings.github_push_poll_seconds
    settings.github_push_poll_seconds = 0
    yield
    settings.github_push_poll_seconds = previous


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


@pytest.fixture(autouse=True)
def no_installed_model_clis(monkeypatch, tmp_path):
    """Model discovery sees no `opencode` or `codex`, whatever this machine has installed.

    `GET /api/workspaces/runtime-options` lists models by running both CLIs. With
    them installed, three tests ran the real `opencode models` — 97s of the suite,
    16s for one assertion on a static list — and saw a different catalog than CI,
    where neither is installed. Each binary's override is pointed at a path that
    does not exist, which the resolvers treat as absent, and `CODEX_HOME` at an
    empty directory so codex's cache fallback reads nothing.

    A test of discovery itself still patches `resolve_*_binary` or
    `subprocess.run`, which takes precedence. The caches are cleared either side
    so a catalog one test faked cannot reach the next.
    """
    missing = tmp_path / "no-installed-cli"
    for adapter in ("opencode", "codex"):
        monkeypatch.setenv(ADAPTER_BINARIES[adapter][1], str(missing))
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex-home"))
    opencode_discovery.reset_model_cache()
    codex_discovery.reset_model_cache()
    yield
    opencode_discovery.reset_model_cache()
    codex_discovery.reset_model_cache()


@pytest.fixture(name="gh_stub", scope="session")
def gh_stub_fixture(tmp_path_factory):
    """A `gh` that fails at once, the way `gh` fails in a repo with no GitHub remote."""
    stub = tmp_path_factory.mktemp("gh-stub") / "gh-disabled-under-pytest"
    stub.write_text(
        "#!/bin/sh\necho 'gh: not available under pytest (tests/conftest.py)' >&2\nexit 1\n",
        encoding="utf-8",
    )
    stub.chmod(0o755)
    return stub


@pytest.fixture(autouse=True)
def no_installed_gh(monkeypatch, gh_stub):
    """No test reaches the `gh` installed on this machine, or GitHub through it.

    Branch triage asks `gh` for every branch's PR. The triage repos have no
    GitHub remote, so each call could only fail, but the real binary took ~4.6s
    to say so: 14 calls, 65s of `test_branch_triage.py`, signed in as whatever
    account this machine has. Every `gh` call goes through `run_gh`, which
    honours `LOREGARDEN_GH_BIN`. A test of a `gh` flow still patches `run_gh`
    or `subprocess.run`, which takes precedence.
    """
    monkeypatch.setenv(GH_BINARY_ENV, str(gh_stub))


@pytest.fixture(autouse=True)
def no_installed_docker(monkeypatch, tmp_path):
    """No test reaches this machine's docker daemon.

    Doctor runs probe docker as a side effect, and the real daemon cost 45s
    across 29 calls, answering with whatever this host's daemon had running.
    Every docker call goes through `run_docker` with `settings.docker_binary`; a
    path that does not exist raises `FileNotFoundError`, which the probe layer
    reports as docker not installed. Docker tests patch above `run_docker` and
    are unaffected.
    """
    monkeypatch.setattr(settings, "docker_binary", str(tmp_path / "no-installed-docker"))


@pytest.fixture(autouse=True)
def _forget_docker_probe():
    """The `docker info` cache is one module-global value with no key. A probe
    another test cached — `NCPU: 0` from the doctor tests — leaked into the
    reaper test on the same xdist worker, and that test's fixed clock made it
    look fresh forever (`now - at` negative), so a 4-cpu waiter never fit and
    was never promoted. Every test starts with no cached probe."""
    docker_capacity.clear_probe_cache()
    yield
    docker_capacity.clear_probe_cache()


@pytest.fixture(autouse=True)
def _host_pool_never_binds(monkeypatch):
    """Every docker claim is charged to the host pool too, and the host ceiling is
    measured from the machine running the suite — 10 cpus here, 2 on a CI runner,
    where the derived ceiling is near zero. A docker test must not pass or fail on
    that, so the host is overridden to a size no test reaches; a test about the
    host pool sets its own ceiling on the row."""
    monkeypatch.setattr(settings, "host_capacity_cpus", 1024.0)
    monkeypatch.setattr(settings, "host_capacity_memory_mb", 1024 * 1024)
    monkeypatch.setattr(settings, "host_capacity_max_leases", 1024)


#: Whether `isolated_db` handed this test the seeded template, for `client` to read.
_SEEDED_FROM_TEMPLATE = pytest.StashKey[bool]()


@pytest.fixture(name="schema_template", scope="session")
def schema_template_fixture(tmp_path_factory):
    """An empty, fully tabled database, built once per process; see `tests/db_templates.py`."""
    return build_schema_template(tmp_path_factory.mktemp("schema-template") / "schema.db")


@pytest.fixture(name="isolated_db", autouse=True)
def isolated_db_fixture(tmp_path, monkeypatch, request, schema_template):
    """Give every test an isolated, schema'd SQLite engine and point all
    module-global engine bindings at it.

    This makes DB-backed code work whether it is reached through the API or
    called directly (a service that spawns a run recorder on the global engine
    now shares the test engine). It does NOT seed — request the ``client``
    fixture, or call ``seed_database(session)``, when a test needs the built-in
    workspace/ticket/agent data.

    A test that requests ``client`` starts from the seeded template instead,
    copied here rather than in ``client``: the test's own fixtures write into
    this database before ``client`` is set up, and a later restore would erase
    them. See `tests/db_templates.py`.
    """
    database = tmp_path / "pytest.db"
    seeded = "client" in request.fixturenames
    source = request.getfixturevalue("seeded_template").database if seeded else schema_template
    copy_database(source, database)
    request.node.stash[_SEEDED_FROM_TEMPLATE] = seeded
    engine = create_engine(
        f"sqlite:///{database}",
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
    for target in _ENGINE_BINDINGS:
        monkeypatch.setattr(target, engine)
    # Chat attachments are files beside the database; keep them beside this one.
    monkeypatch.setattr(settings, "chat_attachments_dir", tmp_path / "chat-attachments")
    return engine


@pytest.fixture(name="seeded_template", scope="session")
def seeded_template_fixture(tmp_path_factory, schema_template) -> SeededTemplate:
    """The database `client` starts from, built once per process: seeded, repointed, migrated.

    In the order the fixture and the app's lifespan always applied them — seed,
    repoint the workspace, then ``init_db`` — because two migrations read
    ``Workspace.repo_path``. Built on its own engine, with every module-global
    engine binding pointed at it for the duration, so nothing reaches a test's
    database or the real one. The lifespan still runs ``init_db`` and
    ``seed_database`` on every copy; on this database both are near no-ops.
    """
    root = tmp_path_factory.mktemp("seeded-template")
    template = SeededTemplate(database=root / "seeded.db", repo=root / "seeded-repo")
    _init_seeded_workspace_repo(template.repo)
    copy_database(schema_template, template.database)
    engine = create_engine(f"sqlite:///{template.database}")
    try:
        with ExitStack() as stack:
            for target in _ENGINE_BINDINGS:
                stack.enter_context(patch(target, engine))
            with Session(engine) as session:
                seed_database(session)
                _repoint_seeded_workspace(session, template.repo)
            init_db()
    finally:
        engine.dispose()
    return template


def _isolate_seeded_workspace_repo(
    session: Session, tmp_path, seeded_template: SeededTemplate
) -> None:
    """Repoint the seeded "loregarden" workspace at a throwaway git repo.

    Orchestration/CLI-executor code paths run real `git checkout -B` against
    a workspace's resolved repo root. Left pointed at repo_path="." (which
    resolves against the real settings.repo_root), tests that orchestrate the
    seeded workspace's tickets would check out branches in the actual project
    working directory. Profile/doc loading falls back to settings.repo_root
    directly and is unaffected by this repo_path change.

    The repo is a copy of one built once per process — the five git calls cost
    more than most tests that use them.
    """
    repo = seeded_template.copy_repo(tmp_path / "loregarden-seeded-repo")
    _repoint_seeded_workspace(session, repo)


def _repoint_seeded_workspace(session: Session, repo) -> None:
    ws = session.exec(select(Workspace).where(Workspace.slug == "loregarden")).first()
    if ws:
        ws.repo_path = str(repo)
        session.add(ws)
        session.commit()


def _init_seeded_workspace_repo(repo) -> None:
    """Build the workspace repo `client` tests copy.

    Called from a session fixture, which runs before `scrub_ambient_git_env`
    applies to anything — so it scrubs the same variables itself, or a
    `GIT_DIR` inherited from a hook would aim every call at the outer checkout.
    """
    env = {k: v for k, v in os.environ.items() if k not in GIT_LOCATION_ENV_VARS}

    def git(*args: str) -> None:
        subprocess.run(["git", *args], cwd=repo, env=env, check=True, capture_output=True)

    repo.mkdir()
    git("init", "-b", "main")
    git("config", "user.email", "test@example.com")
    git("config", "user.name", "Test")
    (repo / "README.md").write_text("# test\n", encoding="utf-8")
    # The dispatch preflight refuses a workspace with no stage-report contract,
    # so a repo standing in for a real workspace has to carry one. Before the
    # commit, so the tree it hands to orchestration is clean.
    seed_stage_report_contract(repo)
    git("add", ".")
    git("commit", "-m", "init")


@pytest.fixture(name="client")
def client_fixture(isolated_db, tmp_path, seeded_template, request):
    def override_session():
        with Session(isolated_db) as session:
            yield session

    app.dependency_overrides[get_session] = override_session
    with Session(isolated_db) as session:
        # Requested dynamically, after `isolated_db` chose the bare schema:
        # seed here, as this fixture always did, rather than hand over an
        # empty database that looks like a seeded one.
        if not request.node.stash.get(_SEEDED_FROM_TEMPLATE, False):
            seed_database(session)
        _isolate_seeded_workspace_repo(session, tmp_path, seeded_template)
    with TestClient(app) as client:
        yield client
    app.dependency_overrides.clear()


@pytest.fixture(name="isolate_seeded_repo")
def isolate_seeded_repo_fixture(tmp_path, seeded_template):
    """Hand a test the repoint that the `client` fixture applies for free.

    A test that seeds the database itself — `isolated_db` plus
    `seed_database(session)`, without going through `client` — leaves the seeded
    workspace on `repo_path=""`, which resolves to the real `settings.repo_root`.
    Any code path that then reaches git operates on the actual project checkout:
    `lg-workflow-integrity-668`'s filesystem probe caught exactly one test doing
    this, writing a preflight probe file into the live `.git` directory.

    Returns a callable rather than doing the work itself, because the isolation
    has to land *after* the test's own `seed_database` call, and a fixture body
    runs before the test.
    """

    def apply(session: Session) -> None:
        _isolate_seeded_workspace_repo(session, tmp_path, seeded_template)

    return apply


@pytest.fixture(name="db_session")
def db_session_fixture(client, isolated_db):
    with Session(isolated_db) as session:
        yield session


@pytest.fixture(autouse=True, scope="session")
def _repo_templates(tmp_path_factory):
    """Give `tests/repo_templates.py` a per-process place to keep its templates."""
    set_template_root(tmp_path_factory.mktemp("repo-templates"))


@pytest.fixture(name="git_repo")
def git_repo_fixture(tmp_path):
    """A throwaway git repo with one commit, for tests that assert on staging."""
    return from_template(tmp_path / "repo", "git_repo", _build_git_repo)


def _build_git_repo(root) -> None:
    root.mkdir()

    def git(*args):
        subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)

    git("init", "-q")
    git("config", "user.email", "t@example.com")
    git("config", "user.name", "Test")
    (root / "seed.txt").write_text("seed\n")
    git("add", "-A")
    git("commit", "-q", "-m", "seed")


@pytest.fixture(autouse=True)
def isolated_instance_registry(tmp_path_factory, monkeypatch):
    """Keep the suite out of the developer's real `~/.lore-eden/instances`.

    Every app lifespan may advertise itself there (`register_main`), and the
    instances endpoints read and launch from it. A shell that exported
    LOREGARDEN_DEV_PORT would otherwise have the suite registering test apps as
    the developer's main server, which every branch client then proxies to.
    """
    monkeypatch.setenv("LORE_EDEN_INSTANCES_DIR", str(tmp_path_factory.mktemp("instances")))
    monkeypatch.setattr(settings, "dev_port", None)
    local_instances.get_registry.cache_clear()
    local_instances.get_instance_manager.cache_clear()
    local_instances.get_template_source.cache_clear()
    yield
    local_instances.get_registry.cache_clear()
    local_instances.get_instance_manager.cache_clear()
    local_instances.get_template_source.cache_clear()


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


@pytest.fixture(autouse=True, scope="session")
def _real_vault_is_off_limits():
    """Every memory store built during the suite, checked at construction.

    `isolated_memory_store` redirects the settings a store resolves its path
    from; this refuses the path itself, wherever it came from. See
    `tests/memory_guard.py` for why the rule is "not the real vault" rather than
    "must be under tmp".

    Session-scoped and patching `__init__` rather than wrapping each call site:
    the leak came from a path nobody was looking at, so the check has to sit
    where every path arrives.
    """
    roots = forbidden_memory_roots()
    if not roots:
        # No vault on this machine — CI, a fresh checkout — so nothing to guard.
        yield
        return

    real_obsidian = ObsidianMemoryStore.__init__
    real_graph = MemoryGraphStore.__init__

    def guarded_obsidian(self, vault_dir):
        reject_if_forbidden("An Obsidian memory store", vault_dir, roots)
        real_obsidian(self, vault_dir)

    def guarded_graph(self, db_path):
        reject_if_forbidden("A memory graph store", db_path, roots)
        real_graph(self, db_path)

    with (
        patch.object(ObsidianMemoryStore, "__init__", guarded_obsidian),
        patch.object(MemoryGraphStore, "__init__", guarded_graph),
    ):
        yield


class _RefusingHttpx:
    """The `httpx` module as `reference_cache` sees it during tests.

    Everything falls through to the real module except `Client`, which is bound
    to a transport that refuses. Proxying rather than listing the attributes is
    deliberate: the service reaches for `httpx.Response`, `httpx.HTTPError`,
    `httpx.TimeoutException` and `httpx.BaseTransport`, and a hand-written
    namespace would have to be kept in step with every one of them — the next
    attribute it reached for would fail as an `AttributeError` inside a module
    whose whole promise is that it never raises.
    """

    def __init__(self, real, client):
        self._real = real
        self.Client = client

    def __getattr__(self, name):
        return getattr(self._real, name)


@pytest.fixture(autouse=True)
def reference_network_refused(monkeypatch):
    """No test reaches the real network through the reference cache.

    Both `fetch_reference` and `search_reference` fetch on a caller's behalf, so
    a test that forgets its transport would quietly make a real request — slow,
    flaky, and dependent on someone else's uptime. Worse, it would *pass*: the
    cache converts a transport failure into a payload, so the only visible
    symptom is a test that occasionally takes ten seconds.

    The refusal happens at **send** time, not construction. `httpx.Client(...)`
    is built outside the cache's `except httpx.TimeoutException/HTTPError`
    handlers, so a constructor that raised would escape them and land on the
    module's outermost boundary as an `INTERNAL_ERROR` — a real refusal
    reported as a bug in the cache. Refusing inside the request puts it where
    the narrow handlers already are, and it comes back as the transport error
    it actually is.

    A test that passes its own `transport=` is untouched, which is how the
    focused suites still exercise real behaviour. `TestClient`'s httpx is
    untouched too: only the name `reference_cache` resolves is replaced.
    """

    # DNS first. `_url_block_reason` resolves the host as an SSRF guard
    # BEFORE any request is built, so patching only httpx left every test in
    # test_search_reference_tool.py making a real lookup for devdocs.io — slow,
    # and dependent on someone else's uptime, which is the exact failure this
    # fixture's docstring says it prevents. It surfaced as 20 failures under a
    # pre-push run whose DNS was unavailable, all reading "devdocs.io did not
    # resolve" rather than the refusal below.
    #
    # A deterministic global address rather than a failure: the guard's own
    # logic (scheme, IP literal, is_global, non-global rejection) still runs, so
    # this hides the network without hiding the check. A test that wants a
    # resolution failure patches getaddrinfo itself, as test_fetch_reference_tool
    # already does.
    def _fake_getaddrinfo(host, port, *args, **kwargs):
        return [(2, 1, 6, "", ("93.184.216.34", port))]

    #
    # On the cache's own seam, not on `socket`: `reference_cache.socket` is the
    # stdlib module, so patching `getaddrinfo` there faked DNS for every test in
    # the suite — even `127.0.0.1` resolved to the address above.
    monkeypatch.setattr(reference_cache, "_getaddrinfo", _fake_getaddrinfo)

    real = reference_cache.httpx

    def refuse(request):
        raise real.ConnectError(
            "the test suite refuses real network through reference_cache; "
            "pass transport= to exercise a fetch"
        )

    class _RefusingClient(real.Client):
        def __init__(self, **kwargs):
            if kwargs.get("transport") is None:
                kwargs["transport"] = real.MockTransport(refuse)
            super().__init__(**kwargs)

    monkeypatch.setattr(reference_cache, "httpx", _RefusingHttpx(real, _RefusingClient))
