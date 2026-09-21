"""Landing a finished ticket on its target (lg-milestone-that-768)."""

from __future__ import annotations

import subprocess
from unittest import mock

import pytest
from loregarden.models.domain import (
    StageStatus,
    Ticket,
    TicketState,
    WorkItemType,
    Workspace,
)
from loregarden.services.land_ticket import LandSkip, land_ticket
from loregarden.services.orchestration import OrchestrationService
from loregarden.services.target_branch import resolve_target_branch
from sqlmodel import Session
from tests.worktree_helpers import (
    at_terminal_stage,
    blocking_text,
    commit_on,
    git,
    make_repo,
)


@pytest.fixture(name="session")
def session_fixture(isolated_db):
    with Session(isolated_db) as session:
        yield session


@pytest.fixture(name="repo")
def repo_fixture(tmp_path):
    return make_repo(tmp_path)


@pytest.fixture(name="workspace")
def workspace_fixture(session, repo):
    ws = Workspace(slug="landing", name="landing", repo_path=str(repo))
    session.add(ws)
    session.commit()
    session.refresh(ws)
    return ws


@pytest.fixture(name="milestone")
def milestone_fixture(session, workspace):
    ms = Ticket(
        external_id="lg-ms-768",
        workspace_id=workspace.id,
        title="Milestone",
        work_item_type=WorkItemType.MILESTONE,
    )
    session.add(ms)
    session.commit()
    session.refresh(ms)
    return ms


def _child(session, workspace, milestone, external_id, **fields):
    ticket = Ticket(
        external_id=external_id,
        workspace_id=workspace.id,
        title=f"Ticket {external_id}",
        branch=f"loregarden/{external_id}",
        parent_ticket_id=milestone.id if milestone else None,
        state=TicketState.IN_PROGRESS,
        **fields,
    )
    session.add(ticket)
    session.commit()
    session.refresh(ticket)
    return ticket


def _sha(cwd, ref="HEAD"):
    return git(cwd, "rev-parse", ref).stdout.strip()


def _parents(repo, sha):
    return git(repo, "rev-list", "--parents", "-n", "1", sha).stdout.split()[1:]


def _ticket_with_work(session, workspace, repo, milestone, external_id, **fields):
    """A child ticket whose branch carries one commit past the integration branch."""
    ticket = _child(session, workspace, milestone, external_id, **fields)
    target = resolve_target_branch(session, ticket, workspace, repo_root=repo)
    git(repo, "branch", ticket.branch, target)
    tip = commit_on(repo, ticket.branch, f"{external_id}.txt", "work\n")
    return ticket, target, tip


# --- land_ticket -----------------------------------------------------------


def test_landing_merges_the_branch_into_the_target_with_a_merge_commit(
    session, workspace, repo, milestone
):
    ticket, target, tip = _ticket_with_work(session, workspace, repo, milestone, "lg-l-1")
    before = _sha(repo, target)

    result = land_ticket(session, ticket, workspace)

    assert result.ok and result.skipped is None
    after = _sha(repo, target)
    assert after != before
    assert result.landed_sha == after
    assert set(_parents(repo, after)) == {before, tip}
    session.refresh(ticket)
    assert ticket.landed_sha == after
    assert ticket.landed_branch == target


def test_landing_is_idempotent(session, workspace, repo, milestone):
    ticket, target, _ = _ticket_with_work(session, workspace, repo, milestone, "lg-l-2")
    first = land_ticket(session, ticket, workspace)
    landed = _sha(repo, target)

    second = land_ticket(session, ticket, workspace)

    assert second.ok and second.skipped is LandSkip.ALREADY_LANDED
    assert _sha(repo, target) == landed, "nothing merged the second time"
    assert first.landed_sha == landed


def test_landing_never_touches_a_checkout(session, workspace, repo, milestone):
    ticket, target, _ = _ticket_with_work(session, workspace, repo, milestone, "lg-l-3")
    git(repo, "checkout", "-q", "-b", "somewhere-else")

    land_ticket(session, ticket, workspace)

    assert git(repo, "branch", "--show-current").stdout.strip() == "somewhere-else"
    assert git(repo, "status", "--porcelain").stdout.strip() == ""
    listed = git(repo, "worktree", "list", "--porcelain").stdout
    assert target not in listed


def test_a_ticket_with_no_branch_has_nothing_to_land(session, workspace, milestone):
    ticket = _child(session, workspace, milestone, "lg-l-4")
    result = land_ticket(session, ticket, workspace)
    assert result.ok and result.skipped is LandSkip.NO_BRANCH
    assert ticket.landed_sha == ""


def test_a_top_level_ticket_is_left_for_the_publish_leg(session, workspace, repo):
    ticket = _child(session, workspace, None, "lg-l-5")
    git(repo, "branch", ticket.branch, "main")
    commit_on(repo, ticket.branch, "solo.txt", "solo\n")
    main_before = _sha(repo, "main")

    result = land_ticket(session, ticket, workspace)

    assert result.ok and result.skipped is LandSkip.BASE_TARGET
    assert _sha(repo, "main") == main_before, "main is never moved locally"


def test_a_conflict_is_returned_with_its_files_and_nothing_is_written(
    session, workspace, repo, milestone
):
    ticket, target, _ = _ticket_with_work(session, workspace, repo, milestone, "lg-l-6")
    commit_on(repo, ticket.branch, "shared.txt", "ticket\n")
    commit_on(repo, target, "shared.txt", "sibling\n")
    before = _sha(repo, target)

    result = land_ticket(session, ticket, workspace)

    assert not result.ok and result.conflicted
    assert result.conflicted_files == ("shared.txt",)
    assert _sha(repo, target) == before
    assert ticket.landed_sha == ""


# --- at the terminal stage ---------------------------------------------------


def _finish(session, ticket):
    """What the orchestrator does when the last stage passes: advance with no route."""
    at_terminal_stage(session, ticket)
    OrchestrationService(session).advance_stage(ticket)
    session.refresh(ticket)


def test_finishing_the_workflow_lands_the_work_and_marks_the_ticket_done(
    session, workspace, repo, milestone
):
    ticket, target, tip = _ticket_with_work(session, workspace, repo, milestone, "lg-l-7")

    _finish(session, ticket)

    assert ticket.state == TicketState.DONE
    assert ticket.landed_branch == target
    assert tip in _parents(repo, ticket.landed_sha)


def test_landing_runs_once_per_finish(session, workspace, repo, milestone):
    ticket, target, _ = _ticket_with_work(session, workspace, repo, milestone, "lg-l-8")

    _finish(session, ticket)
    landed = _sha(repo, target)
    # A second pass over the finished workflow — a requeue, a re-run — lands
    # nothing new.
    OrchestrationService(session).advance_stage(ticket)

    assert _sha(repo, target) == landed
    assert len(git(repo, "rev-list", "--merges", target).stdout.split()) == 1


def test_a_conflict_blocks_the_ticket_naming_the_files(session, workspace, repo, milestone):
    ticket, target, _ = _ticket_with_work(session, workspace, repo, milestone, "lg-l-9")
    commit_on(repo, ticket.branch, "shared.txt", "ticket\n")
    commit_on(repo, target, "shared.txt", "sibling\n")

    _finish(session, ticket)

    assert ticket.state == TicketState.BLOCKED, "not done: the work is not on its target"
    assert ticket.workflow_stage_status == StageStatus.BLOCKED
    assert "shared.txt" in ticket.blocking_issues
    assert ticket.landed_sha == ""


def test_landing_happens_before_done_is_derived(session, workspace, repo, milestone):
    """717's exact failure: the terminal stage wrote `done` a second before the
    orchestration's completion hook looked, and the hook skipped a done
    ticket. Landing has to happen on the way to done, not after it."""
    ticket, target, _ = _ticket_with_work(session, workspace, repo, milestone, "lg-l-10")
    seen: list[TicketState] = []
    from loregarden.services import landing as module

    real = module.land_ticket

    def observe(session_, ticket_, workspace_):
        seen.append(ticket_.state)
        return real(session_, ticket_, workspace_)

    with mock.patch.object(module, "land_ticket", side_effect=observe):
        _finish(session, ticket)

    assert seen == [TicketState.IN_PROGRESS], "landed while still in progress"
    assert ticket.state == TicketState.DONE
    assert ticket.landed_sha


def test_a_git_failure_blocks_with_its_text(session, workspace, repo, milestone):
    ticket, target, _ = _ticket_with_work(session, workspace, repo, milestone, "lg-l-11")
    from loregarden.services import git_merge_noco as module

    real = module.run_git

    def broken(args, **kwargs):
        if args and args[0] == "commit-tree":
            return subprocess.CompletedProcess(
                args, 128, stdout="", stderr="fatal: object store is on fire"
            )
        return real(args, **kwargs)

    with mock.patch.object(module, "run_git", side_effect=broken):
        _finish(session, ticket)

    assert ticket.state == TicketState.BLOCKED
    assert "on fire" in blocking_text(session, ticket), "git's own words"


def test_a_workspace_with_no_repository_finishes_without_landing(session, tmp_path):
    """Test workspaces (and misconfigured ones) point at paths that are not
    repositories. Nothing ran there, so nothing lands — and the workflow must
    still finish rather than block on git's FileNotFoundError."""
    ws = Workspace(slug="no-repo", name="no repo", repo_path=str(tmp_path / "nowhere"))
    session.add(ws)
    session.commit()
    session.refresh(ws)
    ticket = Ticket(
        external_id="lg-l-12", workspace_id=ws.id, title="No repo", state=TicketState.IN_PROGRESS
    )
    session.add(ticket)
    session.commit()
    session.refresh(ticket)

    result = land_ticket(session, ticket, ws)
    assert result.ok and result.skipped is LandSkip.NO_REPOSITORY

    _finish(session, ticket)
    assert ticket.state == TicketState.DONE
