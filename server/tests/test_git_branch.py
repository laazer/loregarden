"""Git branch helpers."""

import subprocess
from pathlib import Path

import pytest
from loregarden.models.domain import Ticket, WorkItemType
from loregarden.services.git_branch import (
    default_ticket_branch,
    ensure_ticket_branch,
    resolve_ticket_branch,
    validate_branch_name,
)


def _init_repo(path: Path) -> None:
    subprocess.run(
        ["git", "init", "-b", "main"], cwd=path, check=True, capture_output=True, text=True
    )
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"],
        cwd=path,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"], cwd=path, check=True, capture_output=True
    )
    (path / "README.md").write_text("# test\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=path, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=path, check=True, capture_output=True)


def test_default_ticket_branch():
    ticket = Ticket(
        external_id="42-my-feature", work_item_type=WorkItemType.TASK, title="x", workspace_id="w"
    )
    assert default_ticket_branch(ticket) == "loregarden/42-my-feature"


def test_resolve_ticket_branch_prefers_explicit():
    ticket = Ticket(
        external_id="42-my-feature",
        branch="custom/branch",
        work_item_type=WorkItemType.TASK,
        title="x",
        workspace_id="w",
    )
    assert resolve_ticket_branch(ticket) == "custom/branch"


def test_validate_branch_name_rejects_invalid():
    with pytest.raises(ValueError):
        validate_branch_name("bad branch name")


def test_ensure_ticket_branch_creates_branch(tmp_path, monkeypatch):
    repo_root = tmp_path / "loregarden"
    repo_root.mkdir()
    _init_repo(repo_root)
    monkeypatch.setenv("LOREGARDEN_REPO_ROOT", str(repo_root))
    monkeypatch.setattr("loregarden.config.settings.repo_root", repo_root.resolve())

    ticket = Ticket(
        external_id="99-test-branch-checkout",
        branch="loregarden/99-test-branch-checkout",
        work_item_type=WorkItemType.TASK,
        title="Branch test",
        workspace_id="w",
    )
    branch = ensure_ticket_branch(repo_root, ticket)
    assert branch == "loregarden/99-test-branch-checkout"
    current = subprocess.run(
        ["git", "branch", "--show-current"],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=True,
    )
    assert current.stdout.strip() == "loregarden/99-test-branch-checkout"
    subprocess.run(["git", "checkout", "-"], cwd=repo_root, check=True, capture_output=True)
