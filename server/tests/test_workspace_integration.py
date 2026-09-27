"""Installing loregarden's hooks and AGENTS.md section into a workspace, from the page.

Driven through the API, running the real installer scripts from this checkout
against the seeded workspace's throwaway repository — so what is asserted is
what lands in that repository's files.
"""

import subprocess
from pathlib import Path
from unittest import mock

import pytest
from fastapi.testclient import TestClient
from loregarden.config import settings
from loregarden.models.domain import Workspace
from loregarden.services import workspace_integration
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
