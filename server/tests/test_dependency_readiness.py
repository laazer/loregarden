"""A dependency is satisfied when its work is reachable, not when its ticket is done (770)."""

from __future__ import annotations

import pytest
from loregarden.models.domain import Ticket, TicketState, WorkItemType, Workspace
from loregarden.services.dependency_readiness import (
    UnmetReason,
    unmet_prerequisites_for_start,
)
from loregarden.services.land_ticket import land_ticket
from loregarden.services.target_branch import (
    TargetBranchError,
    refresh_integration_branch,
    resolve_target_branch,
)
from loregarden.services.ticket_dependencies import TicketDependencyService
from sqlmodel import Session
from tests.worktree_helpers import commit_on, git, make_repo


@pytest.fixture(name="session")
def session_fixture(isolated_db):
    with Session(isolated_db) as session:
        yield session


@pytest.fixture(name="repo")
def repo_fixture(tmp_path):
    return make_repo(tmp_path)


@pytest.fixture(name="workspace")
def workspace_fixture(session, repo):
    ws = Workspace(slug="readiness", name="readiness", repo_path=str(repo))
    session.add(ws)
    session.commit()
    session.refresh(ws)
    return ws


def _ticket(
    session, workspace, external_id, *, parent=None, state=TicketState.IN_PROGRESS, kind=None
):
    ticket = Ticket(
        external_id=external_id,
        workspace_id=workspace.id,
        title=external_id,
        branch=f"loregarden/{external_id}",
        parent_ticket_id=parent.id if parent else None,
        state=state,
        work_item_type=kind or WorkItemType.TASK,
    )
    session.add(ticket)
    session.commit()
    session.refresh(ticket)
    return ticket


def _sha(cwd, ref="HEAD"):
    return git(cwd, "rev-parse", ref).stdout.strip()


def _depends(session, dependent, prerequisite):
    TicketDependencyService(session).add_dependency(dependent.id, prerequisite.id)
    session.commit()


def _reasons(session, ticket, workspace):
    return {
        e.ticket.external_id: e.reason
        for e in unmet_prerequisites_for_start(session, ticket, workspace)
    }


# --- the four readiness outcomes ------------------------------------------------


def test_a_prerequisite_still_in_progress_is_not_done(session, workspace, repo):
    ms = _ticket(session, workspace, "ms", kind=WorkItemType.MILESTONE)
    a = _ticket(session, workspace, "a", parent=ms)
    b = _ticket(session, workspace, "b", parent=ms)
    _depends(session, b, a)

    assert _reasons(session, b, workspace) == {"a": UnmetReason.NOT_DONE}


def test_a_done_prerequisite_whose_branch_never_landed_is_not_landed(session, workspace, repo):
    """The 181 -> 182 case: done, and the dependent cannot see a line of it."""
    ms = _ticket(session, workspace, "ms", kind=WorkItemType.MILESTONE)
    a = _ticket(session, workspace, "a", parent=ms, state=TicketState.DONE)
    b = _ticket(session, workspace, "b", parent=ms)
    _depends(session, b, a)
    target = resolve_target_branch(session, b, workspace, repo_root=repo)
    git(repo, "branch", a.branch, target)
    commit_on(repo, a.branch, "a.txt", "a's work\n")

    unmet = unmet_prerequisites_for_start(session, b, workspace)

    assert [(e.ticket.external_id, e.reason, e.target) for e in unmet] == [
        ("a", UnmetReason.NOT_LANDED, target)
    ]


def test_a_landed_prerequisite_is_satisfied(session, workspace, repo):
    ms = _ticket(session, workspace, "ms", kind=WorkItemType.MILESTONE)
    a = _ticket(session, workspace, "a", parent=ms, state=TicketState.DONE)
    b = _ticket(session, workspace, "b", parent=ms)
    _depends(session, b, a)
    target = resolve_target_branch(session, a, workspace, repo_root=repo)
    git(repo, "branch", a.branch, target)
    commit_on(repo, a.branch, "a.txt", "a's work\n")
    assert _reasons(session, b, workspace) == {"a": UnmetReason.NOT_LANDED}

    assert land_ticket(session, a, workspace).ok

    assert _reasons(session, b, workspace) == {}


def test_a_prerequisite_whose_work_reached_main_by_hand_is_satisfied(session, workspace, repo):
    """Every ticket closed before landing existed has an empty landed_sha. Its
    commits are on main anyway; the column is not the evidence, the graph is."""
    ms = _ticket(session, workspace, "ms", kind=WorkItemType.MILESTONE)
    a = _ticket(session, workspace, "a", state=TicketState.DONE)  # top-level, older
    b = _ticket(session, workspace, "b", parent=ms)
    _depends(session, b, a)
    git(repo, "branch", a.branch, "main")
    commit_on(repo, a.branch, "a.txt", "a's work\n")
    git(repo, "merge", "-q", "--no-ff", "-m", "merged by a person", a.branch)

    assert a.landed_sha == ""
    assert _reasons(session, b, workspace) == {}


def test_wont_do_and_planning_only_and_other_workspace_are_judged_by_state(
    session, workspace, repo
):
    ms = _ticket(session, workspace, "ms", kind=WorkItemType.MILESTONE)
    b = _ticket(session, workspace, "b", parent=ms)
    abandoned = _ticket(session, workspace, "abandoned", state=TicketState.WONT_DO)
    planning = _ticket(session, workspace, "planning", state=TicketState.DONE)  # no branch
    other_ws = Workspace(slug="elsewhere", name="elsewhere", repo_path="/nowhere")
    session.add(other_ws)
    session.commit()
    elsewhere = _ticket(session, other_ws, "elsewhere-1", state=TicketState.DONE)
    for prereq in (abandoned, planning, elsewhere):
        _depends(session, b, prereq)

    assert _reasons(session, b, workspace) == {}


# --- the integration branch keeps up with the base -------------------------------


def test_cutting_a_ticket_brings_its_integration_branch_up_to_main(session, workspace, repo):
    """A prerequisite in another tree reaches main through that tree's publish;
    without this the dependent's integration branch never sees it."""
    ms = _ticket(session, workspace, "ms", kind=WorkItemType.MILESTONE)
    b = _ticket(session, workspace, "b", parent=ms)
    target = resolve_target_branch(session, b, workspace, repo_root=repo)
    before = _sha(repo, target)
    landed_elsewhere = commit_on(repo, "main", "other.txt", "another tree's work\n")

    again = resolve_target_branch(session, b, workspace, repo_root=repo)

    assert again == target
    assert _sha(repo, target) != before
    assert git(repo, "merge-base", "--is-ancestor", landed_elsewhere, target).returncode == 0
    assert git(repo, "branch", "--show-current").stdout.strip() == "main", "no checkout moved"


def test_a_refresh_that_conflicts_refuses_to_cut_the_ticket(session, workspace, repo):
    ms = _ticket(session, workspace, "ms", kind=WorkItemType.MILESTONE)
    b = _ticket(session, workspace, "b", parent=ms)
    target = resolve_target_branch(session, b, workspace, repo_root=repo)
    commit_on(repo, target, "shared.txt", "integration side\n")
    commit_on(repo, "main", "shared.txt", "main side\n")

    with pytest.raises(TargetBranchError, match="shared.txt"):
        resolve_target_branch(session, b, workspace, repo_root=repo)


def test_refresh_is_a_no_op_when_the_base_has_not_moved(repo):
    git(repo, "branch", "integration/x", "main")
    assert refresh_integration_branch(repo, "integration/x", "main") is False
    assert _sha(repo, "integration/x") == _sha(repo, "main")
