"""Installing loregarden's hooks and AGENTS.md section into a workspace, from the page.

Driven through the API, running the real installer scripts from this checkout
against the seeded workspace's throwaway repository — so what is asserted is
what lands in that repository's files.
"""

import os
import subprocess
import sys
from pathlib import Path
from unittest import mock

import pytest
from fastapi.testclient import TestClient
from loregarden.config import settings
from loregarden.models.domain import Workspace
from loregarden.services import workspace_integration
from loregarden.services.git_subprocess import run_git
from loregarden.services.workspace_integration import primary_checkout
from sqlmodel import Session, select

LEFTHOOK = "pre-commit:\n  commands:\n    lint:\n      run: echo lint\n"


@pytest.fixture(name="repo")
def repo_fixture(client: TestClient, isolated_db) -> Path:
    with Session(isolated_db) as session:
        workspace = session.exec(select(Workspace).where(Workspace.slug == "loregarden")).one()
        return Path(workspace.repo_path)


def _states(response) -> dict[str, tuple[str, str]]:
    assert response.status_code == 200, response.text
    return {i["installer"]: (i["state"], i["detail"]) for i in response.json()["installers"]}


def test_a_bare_repository_reports_what_is_missing_and_why(client: TestClient, repo: Path) -> None:
    states = _states(client.get("/api/workspace-integration/loregarden"))
    assert states["docs"][0] == "missing"
    hooks_state, hooks_detail = states["hooks"]
    assert hooks_state == "unavailable"
    assert "no lefthook.yml" in hooks_detail


def test_installing_docs_writes_the_section_with_the_workspace_slug(
    client: TestClient, repo: Path
) -> None:
    states = _states(client.post("/api/workspace-integration/loregarden/docs"))
    assert states["docs"][0] == "current"
    agents = (repo / "AGENTS.md").read_text(encoding="utf-8")
    assert "workspace_slug=loregarden" in agents


def test_installing_hooks_points_the_block_at_the_primary_checkout(
    client: TestClient, repo: Path
) -> None:
    (repo / "lefthook.yml").write_text(LEFTHOOK, encoding="utf-8")
    assert _states(client.get("/api/workspace-integration/loregarden"))["hooks"][0] == "missing"
    states = _states(client.post("/api/workspace-integration/loregarden/hooks"))
    assert states["hooks"][0] == "current"
    config = (repo / "lefthook.yml").read_text(encoding="utf-8")
    assert str(primary_checkout()) in config
    assert "run: echo lint" in config


def test_a_refused_install_says_why(client: TestClient, repo: Path) -> None:
    response = client.post("/api/workspace-integration/loregarden/hooks")
    assert response.status_code == 409
    assert "no lefthook.yml" in response.json()["detail"]


@pytest.mark.parametrize(
    ("path", "code"),
    [
        ("/api/workspace-integration/nope", 404),
        ("/api/workspace-integration/loregarden/shell", 422),
    ],
)
def test_refusals(client: TestClient, repo: Path, path: str, code: int) -> None:
    call = client.get if path.count("/") == 3 else client.post
    assert call(path).status_code == code


def test_outside_a_git_checkout_nothing_is_installed(client: TestClient, repo: Path) -> None:
    with mock.patch.object(workspace_integration, "primary_checkout", return_value=None):
        states = _states(client.get("/api/workspace-integration/loregarden"))
        response = client.post("/api/workspace-integration/loregarden/docs")
    assert {state for state, _ in states.values()} == {"unavailable"}
    assert response.status_code == 409
    assert not (repo / "AGENTS.md").exists()


def test_the_primary_checkout_is_found_from_a_linked_worktree(tmp_path: Path) -> None:
    primary = tmp_path / "primary"
    primary.mkdir()

    def git(*args: str) -> None:
        subprocess.run(["git", *args], cwd=primary, check=True, capture_output=True)

    git("init", "-q", "-b", "main")
    git(
        "-c",
        "user.email=t@example.com",
        "-c",
        "user.name=T",
        "commit",
        "-q",
        "--allow-empty",
        "-m",
        "seed",
    )
    git("worktree", "add", "-q", "-b", "feat", str(tmp_path / "feat"))
    with mock.patch.object(settings, "repo_root", tmp_path / "feat"):
        assert primary_checkout() == primary.resolve()


@pytest.fixture(name="loregarden_repo")
def loregarden_repo_fixture(repo: Path):
    """Loregarden running from the seeded workspace's own repository, real installers and all.

    The scripts are linked in so a missing guard would install rather than fail for want of one.
    """
    (repo / "scripts").symlink_to(Path(__file__).resolve().parents[2] / "scripts")
    with mock.patch.object(settings, "repo_root", repo):
        yield repo


def _point_workspace_at(isolated_db, path: Path) -> None:
    with Session(isolated_db) as session:
        workspace = session.exec(select(Workspace).where(Workspace.slug == "loregarden")).one()
        workspace.repo_path = str(path)
        session.add(workspace)
        session.commit()


def test_loregarden_itself_reports_both_parts_built_in(
    client: TestClient, loregarden_repo: Path
) -> None:
    states = _states(client.get("/api/workspace-integration/loregarden"))
    assert {state for state, _ in states.values()} == {"built_in"}


def test_a_linked_worktree_of_loregarden_is_built_in_too(
    client: TestClient, isolated_db, loregarden_repo: Path, tmp_path: Path
) -> None:
    linked = tmp_path / "linked"
    subprocess.run(
        ["git", "worktree", "add", "-q", "-b", "linked", str(linked)],
        cwd=loregarden_repo,
        check=True,
        capture_output=True,
    )
    _point_workspace_at(isolated_db, linked)
    states = _states(client.get("/api/workspace-integration/loregarden"))
    assert {state for state, _ in states.values()} == {"built_in"}


def test_installing_into_loregarden_itself_is_refused_and_writes_nothing(
    client: TestClient, loregarden_repo: Path
) -> None:
    response = client.post("/api/workspace-integration/loregarden/docs")
    assert response.status_code == 409
    assert not (loregarden_repo / "AGENTS.md").exists()


# -- which Python the installers run ----------------------------------------


def test_the_server_runs_the_installers_with_its_own_interpreter(
    client: TestClient, repo: Path
) -> None:
    """Not whatever `python3` PATH finds: through a pyenv shim that is ~6s a call."""
    real_run = subprocess.run
    with mock.patch.object(workspace_integration.subprocess, "run", wraps=real_run) as spawn:
        _states(client.get("/api/workspace-integration/loregarden"))
    installer_calls = [
        call
        for call in spawn.call_args_list
        if Path(call.args[0][0]).name in {"install-workspace-hooks.sh", "install-workspace-docs.sh"}
    ]
    assert len(installer_calls) == 2
    for call in installer_calls:
        assert call.kwargs["env"][workspace_integration.INSTALLER_PYTHON_ENV] == sys.executable
        # This checkout's scripts, not the primary's: from a worktree the primary
        # is main, and a branch's changes to the installers would go untested.
        assert Path(call.args[0][0]).parent == settings.repo_root / "scripts"


@pytest.mark.parametrize(
    "script", ["install-workspace-hooks.sh", "install-workspace-docs.sh"], ids=["hooks", "docs"]
)
def test_an_installer_runs_python_through_the_interpreter_it_is_given(
    tmp_path: Path, script: str
) -> None:
    target = tmp_path / "workspace"
    target.mkdir()
    run_git(["init", "-q", "-b", "main"], cwd=target, check=True, capture_output=True)
    (target / "lefthook.yml").write_text(LEFTHOOK, encoding="utf-8")
    calls = tmp_path / "python-calls"
    stub = tmp_path / "recording-python"
    stub.write_text(
        f'#!/bin/sh\necho "$@" >> "{calls}"\nexec "{sys.executable}" "$@"\n', encoding="utf-8"
    )
    stub.chmod(0o755)

    completed = subprocess.run(
        [str(settings.repo_root / "scripts" / script), "--check", str(target)],
        env={**os.environ, workspace_integration.INSTALLER_PYTHON_ENV: str(stub)},
        capture_output=True,
        text=True,
        check=False,
    )

    assert calls.exists(), completed.stderr
    assert "--check" in calls.read_text(encoding="utf-8")
    assert completed.stdout.startswith("missing"), completed.stdout + completed.stderr
