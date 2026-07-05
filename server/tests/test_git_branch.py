"""Git branch helpers."""

import subprocess

import pytest

from loregarden.models.domain import Ticket, WorkItemType
from loregarden.services.git_branch import (
    default_ticket_branch,
    ensure_ticket_branch,
    resolve_ticket_branch,
    validate_branch_name,
)


def test_default_ticket_branch():
    ticket = Ticket(external_id="42-my-feature", work_item_type=WorkItemType.TASK, title="x", workspace_id="w")
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


def test_ensure_ticket_branch_creates_branch():
    from loregarden.config import settings

    repo_root = settings.repo_root
    if not (repo_root / ".git").exists():
        pytest.skip("repo root is not a git repository")

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
