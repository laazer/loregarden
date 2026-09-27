"""Loregarden's local-instance templates, and the boot they launch into.

The launcher itself is lore-eden's and tested there against real processes.
What is loregarden's is what the templates hand it — which worktree, which
database, which server a client proxies to — and the sandbox boot that keeps a
branch server off main's runs and worktrees.
"""

import sqlite3
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from lore_eden.instances import (
    FileInstanceRegistry,
    InstanceKind,
    LaunchContext,
    LaunchRequest,
    TemplateParamError,
    register_self,
)
from loregarden import main as main_module
from loregarden.config import settings
from loregarden.main import app
from loregarden.services import local_instances
from loregarden.services.local_instances import (
    ClientTemplate,
    DatabaseMode,
    ServerTemplate,
    list_worktrees,
    register_main,
)


@pytest.fixture(name="checkout")
def checkout_fixture(tmp_path: Path) -> Path:
    """A repository with a server/ and client/ like loregarden's, and one worktree."""
    root = tmp_path / "checkout"
    (root / "server").mkdir(parents=True)
    (root / "server" / "pyproject.toml").write_text("[project]\nname='x'\n", encoding="utf-8")
    (root / "client").mkdir()
    (root / "client" / "package.json").write_text("{}", encoding="utf-8")

    def git(*args: str) -> None:
        subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)

    git("init", "-q", "-b", "main")
    git("config", "user.email", "t@example.com")
    git("config", "user.name", "Test")
    git("add", "-A")
    git("commit", "-q", "-m", "seed")
    git("worktree", "add", "-q", "-b", "feat-x", str(tmp_path / "feat-x"))
    return root


@pytest.fixture(name="main_db")
def main_db_fixture(tmp_path: Path) -> Path:
    path = tmp_path / "main.db"
    with sqlite3.connect(path) as db:
        db.execute("create table tickets (title text)")
        db.execute("insert into tickets values ('from main')")
    return path


@pytest.fixture(name="registry")
def registry_fixture() -> FileInstanceRegistry:
    return local_instances.get_registry()


def _ctx(registry: FileInstanceRegistry, tmp_path: Path) -> LaunchContext:
    instance_dir = tmp_path / "instance"
    instance_dir.mkdir()
    return LaunchContext("loregarden-x-1", instance_dir, registry)


def test_worktrees_are_listed_with_their_branches(checkout: Path, tmp_path: Path) -> None:
    trees = list_worktrees(checkout)
    assert [(t.path.resolve(), t.branch) for t in trees] == [
        (checkout.resolve(), "main"),
        ((tmp_path / "feat-x").resolve(), "feat-x"),
    ]


def test_server_runs_sandboxed_on_a_snapshot_of_main(
    checkout: Path, main_db: Path, registry: FileInstanceRegistry, tmp_path: Path
) -> None:
    ctx = _ctx(registry, tmp_path)
    feat = str((tmp_path / "feat-x").resolve())
    with (
        patch.object(settings, "repo_root", checkout),
        patch.object(settings, "database_url", f"sqlite:///{main_db}"),
    ):
        spec = ServerTemplate().build(
            LaunchRequest(template="server", params={"worktree": feat}), ctx
        )
    assert spec.env["LOREGARDEN_SANDBOX"] == "1"
    assert spec.env["LOREGARDEN_REPO_ROOT"] == feat
    assert spec.env["LOREGARDEN_OBSIDIAN_VAULT_DIR"] == ""
    snapshot = ctx.instance_dir / "loregarden.db"
    assert spec.env["LOREGARDEN_DATABASE_URL"].endswith(str(snapshot))
    with sqlite3.connect(snapshot) as db:
        assert db.execute("select title from tickets").fetchall() == [("from main",)]
    assert spec.labels["branch"] == "feat-x"


def test_a_fresh_server_database_copies_nothing(
    checkout: Path, main_db: Path, registry: FileInstanceRegistry, tmp_path: Path
) -> None:
    ctx = _ctx(registry, tmp_path)
    with (
        patch.object(settings, "repo_root", checkout),
        patch.object(settings, "database_url", f"sqlite:///{main_db}"),
    ):
        ServerTemplate().build(
            LaunchRequest(
                template="server",
                params={"worktree": str(checkout.resolve()), "database": DatabaseMode.FRESH},
            ),
            ctx,
        )
    assert not (ctx.instance_dir / "loregarden.db").exists()


def test_a_path_that_is_not_one_of_the_repos_worktrees_is_refused(
    checkout: Path, registry: FileInstanceRegistry, tmp_path: Path
) -> None:
    with (
        patch.object(settings, "repo_root", checkout),
        pytest.raises(TemplateParamError, match="must be one of"),
    ):
        ServerTemplate().build(
            LaunchRequest(template="server", params={"worktree": "/etc"}), _ctx(registry, tmp_path)
        )


def test_client_against_main_needs_a_registered_main(
    checkout: Path, registry: FileInstanceRegistry, tmp_path: Path
) -> None:
    with (
        patch.object(settings, "repo_root", checkout),
        pytest.raises(TemplateParamError, match="No main server is registered"),
    ):
        ClientTemplate().build(LaunchRequest(template="client"), _ctx(registry, tmp_path))


def test_client_proxies_to_main_when_it_is_registered(
    checkout: Path, registry: FileInstanceRegistry, tmp_path: Path
) -> None:
    main = register_self(
        registry,
        project="loregarden",
        name="main",
        kind=InstanceKind.SERVER,
        host="127.0.0.1",
        port=8000,
    )
    assert main is not None
    with patch.object(settings, "repo_root", checkout):
        spec = ClientTemplate().build(LaunchRequest(template="client"), _ctx(registry, tmp_path))
    assert spec.env == {"LOREGARDEN_API_TARGET": "http://127.0.0.1:8000"}
    assert spec.target_instance_id == main.record.id
    assert spec.cwd == checkout.resolve() / "client"


def test_main_is_advertised_only_when_the_dev_server_says_where_it_is(
    registry: FileInstanceRegistry,
) -> None:
    assert register_main() is None
    with patch.object(settings, "dev_port", 8123):
        handle = register_main()
        assert handle is not None
        assert registry.find_main("loregarden").url == "http://127.0.0.1:8123"
        with patch.object(settings, "sandbox", True):
            assert register_main() is None
        handle.release()


def test_sandbox_boot_skips_recovery_and_the_reconcile_timer(isolated_db) -> None:
    with (
        patch.object(settings, "sandbox", True),
        patch.object(main_module, "_recover_previous_boot") as recover,
        patch.object(main_module, "start_reconcile_loop") as timer,
    ):
        with TestClient(app):
            pass
    recover.assert_not_called()
    timer.assert_not_called()


def test_ordinary_boot_still_recovers(isolated_db) -> None:
    with (
        patch.object(main_module, "_recover_previous_boot") as recover,
        patch.object(main_module, "start_reconcile_loop", return_value=None),
    ):
        with TestClient(app):
            pass
    recover.assert_called_once()


def test_templates_are_served_under_api_instances(client: TestClient) -> None:
    response = client.get("/api/instances/templates")
    assert response.status_code == 200, response.text
    assert sorted(t["name"] for t in response.json()) == ["client", "server"]
    assert client.get("/api/instances").json() == {"instances": [], "unreadable": []}
