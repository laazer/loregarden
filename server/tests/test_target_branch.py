"""Where a ticket's work lands: the integration branch per subtree (767)."""

import subprocess
from pathlib import Path

import pytest
from loregarden.models.domain import Ticket, WorkItemType, Workspace
from loregarden.services.git_subprocess import scrubbed_git_env
from loregarden.services.hierarchy_service import reparent_ticket
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

    # Move main. A second call must not re-create the branch (770 brings it up
    # to main with a merge, which is a different thing from resetting it).
    (repo / "later.txt").write_text("later\n", encoding="utf-8")
    _git(["add", "."], cwd=repo)
    _git(["commit", "-m", "later"], cwd=repo)
    later_sha = _git(["rev-parse", "main"], cwd=repo)

    second = resolve_target_branch(db_session, task, workspace, repo_root=repo)
    assert second == first
    assert _branches(repo) == after_first
    tip = _git(["rev-parse", second], cwd=repo)
    assert tip not in (main_sha, later_sha), "a merge, not a reset to either side"
    parents = _git(["rev-list", "--parents", "-n", "1", tip], cwd=repo).split()[1:]
    assert set(parents) == {main_sha, later_sha}


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


# --- an initiative is never a landing root -----------------------------------------


def _initiative(db_session: Session, external_id: str) -> Ticket:
    """Initiatives span workspaces, so they carry none of their own."""
    initiative = Ticket(
        external_id=external_id,
        title=external_id,
        workspace_id=None,
        work_item_type=WorkItemType.INITIATIVE,
    )
    db_session.add(initiative)
    db_session.commit()
    db_session.refresh(initiative)
    return initiative


def test_a_milestone_under_an_initiative_keeps_its_own_integration_branch(
    db_session: Session, workspace: Workspace
):
    initiative = _initiative(db_session, "init-x-1")
    milestone = _ticket(
        db_session, workspace, "lg-ms-10", parent=initiative, work_item_type=WorkItemType.MILESTONE
    )
    feature = _ticket(
        db_session, workspace, "lg-ft-10", parent=milestone, work_item_type=WorkItemType.FEATURE
    )
    task = _ticket(db_session, workspace, "lg-tk-10", parent=feature)

    assert subtree_root(db_session, task).id == milestone.id
    assert target_branch_name(db_session, task, workspace) == "integration/lg-ms-10"
    assert target_branch_name(db_session, feature, workspace) == "integration/lg-ms-10"
    # The milestone is still the root, so its completion is what publishes.
    assert target_branch_name(db_session, milestone, workspace) == "main"


def test_two_milestones_under_one_initiative_land_on_separate_branches(
    db_session: Session, workspace: Workspace
):
    initiative = _initiative(db_session, "init-x-2")
    first = _ticket(
        db_session, workspace, "lg-ms-11", parent=initiative, work_item_type=WorkItemType.MILESTONE
    )
    second = _ticket(
        db_session, workspace, "lg-ms-12", parent=initiative, work_item_type=WorkItemType.MILESTONE
    )
    a = _ticket(
        db_session, workspace, "lg-ft-11", parent=first, work_item_type=WorkItemType.FEATURE
    )
    b = _ticket(
        db_session, workspace, "lg-ft-12", parent=second, work_item_type=WorkItemType.FEATURE
    )

    assert target_branch_name(db_session, a, workspace) == "integration/lg-ms-11"
    assert target_branch_name(db_session, b, workspace) == "integration/lg-ms-12"


def test_attaching_or_detaching_a_milestone_leaves_its_target_branch_alone(
    db_session: Session, workspace: Workspace
):
    initiative = _initiative(db_session, "init-x-3")
    milestone = _ticket(db_session, workspace, "lg-ms-13", work_item_type=WorkItemType.MILESTONE)
    feature = _ticket(
        db_session, workspace, "lg-ft-13", parent=milestone, work_item_type=WorkItemType.FEATURE
    )
    task = _ticket(db_session, workspace, "lg-tk-13", parent=feature)

    def targets() -> list[str]:
        return [target_branch_name(db_session, t, workspace) for t in (milestone, feature, task)]

    before = targets()
    assert before == ["main", "integration/lg-ms-13", "integration/lg-ms-13"]

    reparent_ticket(db_session, milestone, initiative.id)
    db_session.commit()
    assert targets() == before

    reparent_ticket(db_session, milestone, None)
    db_session.commit()
    assert targets() == before


def test_a_feature_filed_directly_under_an_initiative_roots_its_own_tree(
    db_session: Session, workspace: Workspace
):
    """The hierarchy allows a feature or bug straight under an initiative."""
    initiative = _initiative(db_session, "init-x-4")
    feature = _ticket(
        db_session, workspace, "lg-ft-14", parent=initiative, work_item_type=WorkItemType.FEATURE
    )
    capability = _ticket(
        db_session, workspace, "lg-cp-14", parent=feature, work_item_type=WorkItemType.CAPABILITY
    )

    assert target_branch_name(db_session, feature, workspace) == "main"
    assert target_branch_name(db_session, capability, workspace) == "integration/lg-ft-14"


def test_an_initiative_passed_in_directly_is_refused(db_session: Session, workspace: Workspace):
    initiative = _initiative(db_session, "init-x-5")

    with pytest.raises(TargetBranchError, match="init-x-5"):
        subtree_root(db_session, initiative)
    with pytest.raises(TargetBranchError, match="init-x-5"):
        integration_branch_for(initiative)
    with pytest.raises(TargetBranchError, match="init-x-5"):
        target_branch_name(db_session, initiative, workspace)


def test_a_type_the_hierarchy_forbids_under_an_initiative_is_refused(
    db_session: Session, workspace: Workspace
):
    """A task written straight under an initiative (bypassing the hierarchy
    checks) has no tree to land in; it must not borrow the initiative's."""
    initiative = _initiative(db_session, "init-x-6")
    stray = _ticket(db_session, workspace, "lg-tk-16", parent=initiative)

    with pytest.raises(TargetBranchError, match="lg-tk-16"):
        target_branch_name(db_session, stray, workspace)


def test_no_ticket_under_an_initiative_resolves_a_branch_named_after_it(
    db_session: Session, workspace: Workspace, repo: Path
):
    """Every legal shape under an initiative, resolved for real against git."""
    initiative = _initiative(db_session, "init-x-7")
    tickets: list[Ticket] = []
    for m in range(2):
        ms = _ticket(
            db_session,
            workspace,
            f"ms-7{m}",
            parent=initiative,
            work_item_type=WorkItemType.MILESTONE,
        )
        ft = _ticket(
            db_session, workspace, f"ft-7{m}", parent=ms, work_item_type=WorkItemType.FEATURE
        )
        cp = _ticket(
            db_session, workspace, f"cp-7{m}", parent=ft, work_item_type=WorkItemType.CAPABILITY
        )
        tk = _ticket(db_session, workspace, f"tk-7{m}", parent=cp)
        bug = _ticket(db_session, workspace, f"bg-7{m}", parent=ms, work_item_type=WorkItemType.BUG)
        tickets += [ms, ft, cp, tk, bug]
    direct_ft = _ticket(
        db_session, workspace, "ft-7d", parent=initiative, work_item_type=WorkItemType.FEATURE
    )
    direct_bug = _ticket(
        db_session, workspace, "bg-7d", parent=initiative, work_item_type=WorkItemType.BUG
    )
    direct_cp = _ticket(
        db_session, workspace, "cp-7d", parent=direct_ft, work_item_type=WorkItemType.CAPABILITY
    )
    tickets += [direct_ft, direct_bug, direct_cp]

    resolved = {
        t.external_id: resolve_target_branch(db_session, t, workspace, repo_root=repo)
        for t in tickets
    }

    assert all(initiative.external_id not in branch for branch in resolved.values()), resolved
    assert not any(initiative.external_id in b for b in _branches(repo))
    assert {b for b in _branches(repo) if b.startswith("integration/")} == {
        "integration/ms-70",
        "integration/ms-71",
        "integration/ft-7d",
    }
