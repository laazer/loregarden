"""Creating a workspace's repository from the page, and saying what is at a path first.

Driven through the API with the real installer scripts, against directories in
``tmp_path`` — so what is asserted is the repository that lands on disk.
"""

import os
import subprocess
from pathlib import Path
from unittest import mock

import pytest
from fastapi.testclient import TestClient
from loregarden.services import workspace_integration, workspace_repository
from loregarden.services.workspace_integration import InstallerError

GIT_IDENTITY = {
    "GIT_AUTHOR_NAME": "T",
    "GIT_AUTHOR_EMAIL": "t@example.com",
    "GIT_COMMITTER_NAME": "T",
    "GIT_COMMITTER_EMAIL": "t@example.com",
}


@pytest.fixture(autouse=True)
def _identity_and_no_lefthook():
    """A commit identity CI may lack, and no real ``lefthook install`` against a temp repo."""
    with (
        mock.patch.dict(os.environ, GIT_IDENTITY),
        mock.patch.object(workspace_repository.shutil, "which", return_value=None),
    ):
        yield


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True
    ).stdout


def _add_workspace(client: TestClient, path: Path, slug: str = "tinker") -> None:
    response = client.post(
        "/api/workspaces", json={"slug": slug, "name": "Tinker", "repo_path": str(path)}
    )
    assert response.status_code == 201, response.text


def _probe(client: TestClient, path: Path) -> str:
    response = client.get("/api/workspaces/repository-probe", params={"path": str(path)})
    assert response.status_code == 200, response.text
    return response.json()["state"]


def test_the_probe_names_each_kind_of_path(client: TestClient, tmp_path: Path) -> None:
    (tmp_path / "empty").mkdir()
    (tmp_path / "busy").mkdir()
    (tmp_path / "busy" / "notes.txt").write_text("x", encoding="utf-8")
    (tmp_path / "repo").mkdir()
    _git(tmp_path / "repo", "init", "-q")
    assert _probe(client, tmp_path / "nope" / "deeper") == "missing"
    assert _probe(client, tmp_path / "empty") == "empty"
    assert _probe(client, tmp_path / "busy") == "not_a_repository"
    assert _probe(client, tmp_path / "busy" / "notes.txt") == "not_a_repository"
    assert _probe(client, tmp_path / "repo") == "repository"
    assert _probe(client, tmp_path / "repo" / "sub") == "inside_repository"


def test_a_missing_path_becomes_a_committed_repository_with_both_blocks(
    client: TestClient, tmp_path: Path
) -> None:
    root = tmp_path / "new" / "tinker"
    _add_workspace(client, root)
    listed = {w["slug"]: w for w in client.get("/api/workspaces").json()}
    assert listed["tinker"]["repo_state"] == "missing"

    response = client.post("/api/workspaces/tinker/repository")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["repo_state"] == "repository"
    assert "lefthook install" in body["follow_up"]

    assert _git(root, "rev-parse", "--abbrev-ref", "HEAD").strip() == "main"
    assert _git(root, "rev-list", "--count", "HEAD").strip() == "1"
    assert _git(root, "status", "--porcelain") == ""
    assert set(_git(root, "ls-files").split()) == {"AGENTS.md", "lefthook.yml"}
    integration = client.get("/api/workspace-integration/tinker").json()
    assert {i["state"] for i in integration["installers"]} == {"current"}


def test_an_empty_directory_is_initialized_in_place(client: TestClient, tmp_path: Path) -> None:
    root = tmp_path / "tinker"
    root.mkdir()
    _add_workspace(client, root)
    assert client.post("/api/workspaces/tinker/repository").status_code == 200
    assert _git(root, "rev-list", "--count", "HEAD").strip() == "1"


@pytest.mark.parametrize("kind", ["busy", "existing_repo", "inside_repo"])
def test_refused_paths_are_left_untouched(client: TestClient, tmp_path: Path, kind: str) -> None:
    outer = tmp_path / "outer"
    outer.mkdir()
    if kind == "busy":
        root = outer
        (root / "notes.txt").write_text("x", encoding="utf-8")
    elif kind == "existing_repo":
        root = outer
        _git(root, "init", "-q")
    else:
        _git(outer, "init", "-q")
        root = outer / "sub"
    before = sorted(p.relative_to(tmp_path) for p in tmp_path.rglob("*"))
    _add_workspace(client, root)
    response = client.post("/api/workspaces/tinker/repository")
    assert response.status_code == 409
    assert sorted(p.relative_to(tmp_path) for p in tmp_path.rglob("*")) == before


@pytest.mark.parametrize("existed", [False, True])
def test_a_failed_install_rolls_back_only_what_was_created(
    client: TestClient, tmp_path: Path, existed: bool
) -> None:
    root = tmp_path / "new" / "tinker"
    if existed:
        root.mkdir(parents=True)
    _add_workspace(client, root)
    with mock.patch.object(
        workspace_integration, "install", side_effect=InstallerError("docs refused")
    ):
        response = client.post("/api/workspaces/tinker/repository")
    assert response.status_code == 409
    assert "docs refused" in response.json()["detail"]
    if existed:
        assert root.is_dir() and not any(root.iterdir())
    else:
        assert not (tmp_path / "new").exists()


def test_an_unknown_workspace_is_404(client: TestClient) -> None:
    assert client.post("/api/workspaces/nope/repository").status_code == 404
