"""A profile that names no base branch means the repository's default branch.

Loremaker's default is `master`, and its profile (the fallback `default.yaml`)
names none, so the schema's `"main"` sent every tree there to a base that did
not exist: "Cannot create 'integration/…': base branch 'main' does not exist".
"""

from __future__ import annotations

import logging
from pathlib import Path
from unittest import mock

import pytest
from loregarden.models.domain import Workspace
from loregarden.services import git_automation_config, repo_default_branch
from loregarden.services.git_automation_config import base_branch, resolve_git_automation
from loregarden.services.orchestration_profile import GitAutomationConfig, OrchestrationProfile
from loregarden.services.repo_default_branch import FALLBACK_BRANCH, detect_default_branch
from tests.worktree_helpers import git, make_repo


def _repo(tmp_path: Path, name: str, branch: str) -> Path:
    """A one-commit repository whose only branch is ``branch``."""
    repo = make_repo(tmp_path, name)
    git(repo, "branch", "-m", "main", branch)
    return repo


@pytest.fixture(autouse=True)
def _fresh_warnings():
    with (
        mock.patch.object(repo_default_branch, "_warned", set()),
        mock.patch.object(repo_default_branch, "_cache", {}),
    ):
        yield


@pytest.fixture
def master_repo(tmp_path: Path) -> Path:
    return _repo(tmp_path, "master-repo", "master")


@pytest.fixture
def cloned_trunk(tmp_path: Path) -> Path:
    """A clone whose remote's default is `trunk`, with `main` also present locally."""
    origin = _repo(tmp_path, "origin", "trunk")
    git(origin, "branch", "main")
    clone = tmp_path / "clone"
    git(tmp_path, "clone", "-q", str(origin), str(clone))
    git(clone, "branch", "main", "origin/main")
    return clone


def test_a_master_only_repository_defaults_to_master(master_repo: Path):
    assert detect_default_branch(master_repo) == "master"


def test_origin_head_wins_over_a_local_main(cloned_trunk: Path):
    assert detect_default_branch(cloned_trunk) == "trunk"


def test_origin_head_without_a_local_branch_falls_through(cloned_trunk: Path):
    git(cloned_trunk, "checkout", "-q", "main")
    git(cloned_trunk, "branch", "-D", "trunk")
    assert detect_default_branch(cloned_trunk) == "main"


def test_main_breaks_a_tie_with_master(master_repo: Path):
    git(master_repo, "branch", "main")
    assert detect_default_branch(master_repo) == "main"


def test_an_unreadable_repository_falls_back_and_says_so_once(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
):
    missing = tmp_path / "not-a-repo"
    missing.mkdir()
    with caplog.at_level(logging.WARNING, logger=repo_default_branch.__name__):
        assert detect_default_branch(missing) == FALLBACK_BRANCH
        assert detect_default_branch(missing) == FALLBACK_BRANCH
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert str(missing) in warnings[0].getMessage()


def test_a_missing_directory_falls_back(tmp_path: Path):
    assert detect_default_branch(tmp_path / "gone") == FALLBACK_BRANCH


def test_the_answer_is_reused_until_it_expires(master_repo: Path):
    with mock.patch.object(repo_default_branch, "time") as clock:
        clock.monotonic.return_value = 1000.0
        assert repo_default_branch.default_branch(master_repo) == "master"
        git(master_repo, "branch", "-m", "master", "main")
        assert repo_default_branch.default_branch(master_repo) == "master"
        clock.monotonic.return_value = 1000.0 + repo_default_branch.CACHE_SECONDS
        assert repo_default_branch.default_branch(master_repo) == "main"


def test_a_repository_with_no_conventional_branch_falls_back(tmp_path: Path):
    repo = _repo(tmp_path, "odd", "develop")
    assert detect_default_branch(repo) == FALLBACK_BRANCH


def _workspace(repo: Path) -> Workspace:
    return Workspace(id="ws-default-branch", slug="default-branch", name="D", repo_path=str(repo))


def _profile(git: GitAutomationConfig) -> mock._patch:
    return mock.patch.object(
        git_automation_config,
        "resolve_orchestration_profile",
        return_value=OrchestrationProfile(slug="p", name="P", git=git),
    )


def test_a_profile_without_a_base_uses_the_repository_default(master_repo: Path):
    workspace = _workspace(master_repo)
    with _profile(GitAutomationConfig()):
        assert base_branch(workspace) == "master"
        assert resolve_git_automation(workspace).base_branch == "master"


def test_a_profile_that_names_a_base_is_taken_as_written(master_repo: Path):
    workspace = _workspace(master_repo)
    with _profile(GitAutomationConfig(base_branch="main")):
        assert base_branch(workspace) == "main"
        assert resolve_git_automation(workspace).base_branch == "main"
