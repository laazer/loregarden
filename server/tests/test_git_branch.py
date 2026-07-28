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
from loregarden.services.git_subprocess import scrubbed_git_env


def _git(args: list[str], *, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
        env=scrubbed_git_env(),
    )


def _init_repo(path: Path) -> None:
    _git(["init", "-b", "main"], cwd=path)
    _git(["config", "user.email", "test@example.com"], cwd=path)
    _git(["config", "user.name", "Test"], cwd=path)
    (path / "README.md").write_text("# test\n", encoding="utf-8")
    _git(["add", "."], cwd=path)
    _git(["commit", "-m", "init"], cwd=path)


def test_default_ticket_branch():
    ticket = Ticket(
        external_id="42-my-feature", work_item_type=WorkItemType.TASK, title="x", workspace_id="w"
    )
    assert default_ticket_branch(ticket) == "loregarden/42-my-feature"


def test_default_ticket_branch_uses_milestone_prefix():
    ticket = Ticket(
        external_id="42-my-feature",
        milestone="Q3 Launch",
        work_item_type=WorkItemType.TASK,
        title="x",
        workspace_id="w",
    )
    assert default_ticket_branch(ticket) == "q3-launch/42-my-feature"


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
    current = _git(["branch", "--show-current"], cwd=repo_root)
    assert current.stdout.strip() == "loregarden/99-test-branch-checkout"
    _git(["checkout", "-"], cwd=repo_root)


def test_ensure_ticket_branch_self_repairs_worktree_lock(tmp_path, monkeypatch):
    repo_root = tmp_path / "loregarden"
    repo_root.mkdir()
    _init_repo(repo_root)
    monkeypatch.setenv("LOREGARDEN_REPO_ROOT", str(repo_root))
    monkeypatch.setattr("loregarden.config.settings.repo_root", repo_root.resolve())

    ticket = Ticket(
        external_id="88-worktree-lock",
        branch="loregarden/88-worktree-lock",
        work_item_type=WorkItemType.TASK,
        title="Worktree lock",
        workspace_id="w",
    )
    ensure_ticket_branch(repo_root, ticket)
    _git(["checkout", "main"], cwd=repo_root)
    other = tmp_path / "other-worktree"
    _git(["worktree", "add", str(other), "loregarden/88-worktree-lock"], cwd=repo_root)

    branch = ensure_ticket_branch(repo_root, ticket)
    assert branch == "loregarden/88-worktree-lock"
    current = _git(["branch", "--show-current"], cwd=repo_root)
    assert current.stdout.strip() == "loregarden/88-worktree-lock"
    assert not other.exists()


def test_ensure_ticket_branch_refuses_to_remove_primary_lock(tmp_path, monkeypatch):
    from loregarden.services import git_branch as git_branch_mod

    repo_root = tmp_path / "loregarden"
    repo_root.mkdir()
    _init_repo(repo_root)
    monkeypatch.setenv("LOREGARDEN_REPO_ROOT", str(repo_root))
    monkeypatch.setattr("loregarden.config.settings.repo_root", repo_root.resolve())

    ticket = Ticket(
        external_id="primary-lock",
        branch="loregarden/primary-lock",
        work_item_type=WorkItemType.TASK,
        title="Primary",
        workspace_id="w",
    )

    def boom(*_args, **_kwargs):
        raise subprocess.CalledProcessError(
            128,
            ["git", "checkout", "-B", "loregarden/primary-lock"],
            stderr=f"fatal: 'loregarden/primary-lock' is already used by worktree at '{repo_root.resolve()}'",
        )

    monkeypatch.setattr(git_branch_mod, "_checkout_branch", boom)
    with pytest.raises(ValueError, match="already checked out in another worktree"):
        ensure_ticket_branch(repo_root, ticket)
