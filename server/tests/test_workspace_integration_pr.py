"""Landing loregarden's AGENTS.md section or hook block in a workspace as a pull request.

The real installer scripts run in `change_pr`'s throwaway worktree of the seeded
workspace's repository, which is given a bare origin; only `gh` is stubbed. So
what is asserted is what lands on the pushed branch, and that the workspace's
own checkout is left alone.
"""

import subprocess
from pathlib import Path
from unittest import mock

import pytest
from fastapi.testclient import TestClient
from loregarden.models.domain import Workspace
from loregarden.services import change_pr, workspace_integration
from loregarden.services.git_subprocess import GhAccountUnavailable, scrubbed_git_env
from sqlmodel import Session, select

PR_URL = "https://github.com/o/r/pull/9"
LEFTHOOK = "pre-commit:\n  commands:\n    lint:\n      run: echo lint\n"


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, env=scrubbed_git_env(), capture_output=True, text=True, check=True
    ).stdout.strip()


def _gh_ok(*_args, **_kwargs) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess([], 0, stdout=f"{PR_URL}\n", stderr="")


@pytest.fixture(name="repo")
def repo_fixture(client: TestClient, isolated_db, tmp_path: Path) -> Path:
    """The seeded workspace's repository, committed and pushed to a bare origin."""
    with Session(isolated_db) as session:
        workspace = session.exec(select(Workspace).where(Workspace.slug == "loregarden")).one()
        repo = Path(workspace.repo_path)
    for key, value in (("user.email", "t@example.com"), ("user.name", "t")):
        _git(repo, "config", key, value)
    (repo / "lefthook.yml").write_text(LEFTHOOK, encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "init", "--allow-empty")
    origin = tmp_path / "origin.git"
    _git(tmp_path, "init", "-q", "--bare", str(origin))
    _git(repo, "remote", "add", "origin", str(origin))
    _git(repo, "push", "-q", "origin", "HEAD:refs/heads/main")
    _git(origin, "symbolic-ref", "HEAD", "refs/heads/main")
    _git(repo, "fetch", "-q", "origin")
    return repo


def _origin(repo: Path) -> Path:
    return Path(_git(repo, "remote", "get-url", "origin"))


def _pushed_file(repo: Path, branch: str, name: str) -> str:
    return _git(_origin(repo), "show", f"{branch}:{name}")


def _post(client: TestClient, installer: str):
    with mock.patch.object(change_pr, "run_gh", side_effect=_gh_ok) as gh:
        response = client.post(f"/api/workspace-integration/loregarden/{installer}/pull-request")
    return response, gh


def test_opens_a_pull_request_carrying_the_docs_section(client: TestClient, repo: Path) -> None:
    response, gh = _post(client, "docs")

    assert response.status_code == 200, response.text
    assert response.json() == {"outcome": "opened", "detail": PR_URL}
    branch = next(arg for arg in gh.call_args.args[0] if arg.startswith("loregarden/refresh-docs-"))
    assert "workspace_slug=loregarden" in _pushed_file(repo, branch, "AGENTS.md")


def test_never_touches_the_workspace_checkout(client: TestClient, repo: Path) -> None:
    before = _git(repo, "status", "--porcelain")

    _post(client, "docs")

    assert not (repo / "AGENTS.md").exists()
    assert _git(repo, "status", "--porcelain") == before


def test_refreshes_the_hook_block_on_the_default_branch(client: TestClient, repo: Path) -> None:
    response, gh = _post(client, "hooks")

    assert response.json()["outcome"] == "opened", response.text
    branch = next(
        arg for arg in gh.call_args.args[0] if arg.startswith("loregarden/refresh-hooks-")
    )
    pushed = _pushed_file(repo, branch, "lefthook.yml")
    assert "workspace-gates.sh" in pushed
    assert "run: echo lint" in pushed


def test_a_block_already_on_the_default_branch_opens_nothing(
    client: TestClient, repo: Path
) -> None:
    first, _ = _post(client, "docs")
    assert first.json()["outcome"] == "opened", first.text
    # Land the first PR's change on main, as merging it would.
    pushed = next(
        line.split()[-1].removeprefix("refs/heads/")
        for line in _git(_origin(repo), "for-each-ref", "refs/heads/loregarden").splitlines()
    )
    _git(_origin(repo), "update-ref", "refs/heads/main", f"refs/heads/{pushed}")

    again, gh = _post(client, "docs")

    assert again.json()["outcome"] == "no_change", again.text
    gh.assert_not_called()


def test_loregarden_itself_is_refused(client: TestClient, repo: Path) -> None:
    with mock.patch.object(workspace_integration, "is_loregarden", return_value=True):
        response, gh = _post(client, "docs")
    assert response.status_code == 409
    gh.assert_not_called()


def test_an_unusable_github_account_is_refused_before_anything_is_made(
    client: TestClient, repo: Path
) -> None:
    with mock.patch(
        "loregarden.api.workspace_integration.configured_gh_token",
        side_effect=GhAccountUnavailable("gh has no token for 'nobody'"),
    ):
        response, gh = _post(client, "docs")
    assert response.status_code == 412
    assert "nobody" in response.json()["detail"]
    gh.assert_not_called()
    assert _git(_origin(repo), "for-each-ref", "refs/heads/loregarden") == ""


def test_a_failed_step_is_a_bad_gateway_naming_it(client: TestClient, repo: Path) -> None:
    failed = subprocess.CompletedProcess([], 1, stdout="", stderr="gh: HTTP 401")
    with mock.patch.object(change_pr, "run_gh", return_value=failed):
        response = client.post("/api/workspace-integration/loregarden/docs/pull-request")
    assert response.status_code == 502
    assert "401" in response.json()["detail"]
