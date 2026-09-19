"""Where a ticket's work lands: the integration branch per subtree (767)."""

import subprocess
from pathlib import Path

import pytest
from loregarden.models.domain import Ticket, WorkItemType, Workspace
from loregarden.services.git_subprocess import scrubbed_git_env
from loregarden.services.target_branch import (
    TargetBranchError,
    ensure_integration_branch,
    integration_branch_for,
    resolve_target_branch,
    subtree_root,
    target_branch_name,
)
from sqlmodel import Session


def _git(args: list[str], *, cwd: Path) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
        env=scrubbed_git_env(),
    ).stdout.strip()


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    path = tmp_path / "repo"
    path.mkdir()
    _git(["init", "-b", "main"], cwd=path)
    _git(["config", "user.email", "test@example.com"], cwd=path)
    _git(["config", "user.name", "Test"], cwd=path)
    (path / "README.md").write_text("# test\n", encoding="utf-8")
    _git(["add", "."], cwd=path)
    _git(["commit", "-m", "init"], cwd=path)
    return path


@pytest.fixture
def workspace(db_session: Session, repo: Path) -> Workspace:
    # No profile file matches this slug, so the profile falls back to the
    # default and `git.base_branch` is "main".
    ws = Workspace(id="ws-target", slug="target-branch-test", name="Target", repo_path=str(repo))
    db_session.add(ws)
    db_session.commit()
    return ws


def _ticket(
    db_session: Session,
    workspace: Workspace,
    external_id: str,
    *,
    parent: Ticket | None = None,
    work_item_type: WorkItemType = WorkItemType.TASK,
) -> Ticket:
    ticket = Ticket(
        external_id=external_id,
        title=external_id,
        workspace_id=workspace.id,
        work_item_type=work_item_type,
        parent_ticket_id=parent.id if parent else None,
    )
    db_session.add(ticket)
    db_session.commit()
    db_session.refresh(ticket)
    return ticket


def _branches(repo: Path) -> set[str]:
    return set(_git(["for-each-ref", "--format=%(refname:short)", "refs/heads"], cwd=repo).split())


def test_subtree_root_walks_to_the_topmost_ancestor(db_session: Session, workspace: Workspace):
    milestone = _ticket(db_session, workspace, "ms-1", work_item_type=WorkItemType.MILESTONE)
    feature = _ticket(db_session, workspace, "ft-1", parent=milestone)
    task = _ticket(db_session, workspace, "tk-1", parent=feature)

    assert subtree_root(db_session, task).id == milestone.id
    assert subtree_root(db_session, feature).id == milestone.id
    assert subtree_root(db_session, milestone).id == milestone.id


def test_a_top_level_ticket_targets_the_base_branch(db_session: Session, workspace: Workspace):
    solo = _ticket(db_session, workspace, "solo-1")
    assert target_branch_name(db_session, solo, workspace) == "main"


def test_the_root_of_a_tree_targets_the_base_branch(db_session: Session, workspace: Workspace):
    """The tree goes to main once, as a whole — its root is what lands there."""
    milestone = _ticket(db_session, workspace, "ms-2", work_item_type=WorkItemType.MILESTONE)
    _ticket(db_session, workspace, "tk-2", parent=milestone)
    assert target_branch_name(db_session, milestone, workspace) == "main"


def test_every_descendant_shares_the_root_integration_branch(
    db_session: Session, workspace: Workspace
):
    milestone = _ticket(db_session, workspace, "lg-ms-3", work_item_type=WorkItemType.MILESTONE)
    feature = _ticket(db_session, workspace, "lg-ft-3", parent=milestone)
    task = _ticket(db_session, workspace, "lg-tk-3", parent=feature)

    assert integration_branch_for(milestone) == "integration/lg-ms-3"
    assert target_branch_name(db_session, feature, workspace) == "integration/lg-ms-3"
    assert target_branch_name(db_session, task, workspace) == "integration/lg-ms-3"


def test_resolve_creates_the_integration_branch_from_base_once(
    db_session: Session, workspace: Workspace, repo: Path
):
    milestone = _ticket(db_session, workspace, "lg-ms-4", work_item_type=WorkItemType.MILESTONE)
    task = _ticket(db_session, workspace, "lg-tk-4", parent=milestone)
    main_sha = _git(["rev-parse", "main"], cwd=repo)

    first = resolve_target_branch(db_session, task, workspace, repo_root=repo)
    assert first == "integration/lg-ms-4"
    assert _git(["rev-parse", first], cwd=repo) == main_sha
    after_first = _branches(repo)

    # Move main so a second call that re-created the branch would be visible.
    (repo / "later.txt").write_text("later\n", encoding="utf-8")
    _git(["add", "."], cwd=repo)
    _git(["commit", "-m", "later"], cwd=repo)

    second = resolve_target_branch(db_session, task, workspace, repo_root=repo)
    assert second == first
    assert _branches(repo) == after_first
    assert _git(["rev-parse", second], cwd=repo) == main_sha


def test_resolve_never_creates_the_base_branch(
    db_session: Session, workspace: Workspace, repo: Path
):
    solo = _ticket(db_session, workspace, "solo-5")
    before = _branches(repo)
    assert resolve_target_branch(db_session, solo, workspace, repo_root=repo) == "main"
    assert _branches(repo) == before


def test_creation_does_not_move_any_checkout(db_session: Session, workspace: Workspace, repo: Path):
    milestone = _ticket(db_session, workspace, "lg-ms-6", work_item_type=WorkItemType.MILESTONE)
    task = _ticket(db_session, workspace, "lg-tk-6", parent=milestone)
    _git(["checkout", "-q", "-b", "elsewhere"], cwd=repo)

    resolve_target_branch(db_session, task, workspace, repo_root=repo)

    assert _git(["rev-parse", "--abbrev-ref", "HEAD"], cwd=repo) == "elsewhere"


def test_a_missing_base_is_an_error_not_an_empty_branch(repo: Path):
    with pytest.raises(TargetBranchError, match="does not exist"):
        ensure_integration_branch(repo, "integration/x", "no-such-base")
    assert "integration/x" not in _branches(repo)
