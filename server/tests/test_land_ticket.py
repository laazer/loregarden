"""Landing a finished ticket on its target (lg-milestone-that-768)."""

from __future__ import annotations

import json
import subprocess
from unittest import mock

import pytest
from loregarden.models.domain import (
    OrchestrationRun,
    OrchestrationRunStatus,
    Ticket,
    TicketState,
    WorkItemType,
    Workspace,
)
from loregarden.services.land_ticket import LandSkip, land_ticket
from loregarden.services.orchestration_callbacks import OrchestrationCallbackService
from loregarden.services.target_branch import resolve_target_branch
from loregarden.services.ticket_worktree import resolve_execution_root
from sqlmodel import Session
from tests.worktree_helpers import git, make_repo, make_run


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


def _commit_on(repo, branch, filename, content):
    tree = repo.parent / f"tmp-{branch.replace('/', '-')}"
    git(repo, "worktree", "add", "-q", str(tree), branch)
    (tree / filename).write_text(content)
    git(tree, "add", "-A")
    git(tree, "commit", "-q", "-m", f"{filename} on {branch}")
    sha = _sha(tree)
    git(repo, "worktree", "remove", "--force", str(tree))
    return sha


def _parents(repo, sha):
    return git(repo, "rev-list", "--parents", "-n", "1", sha).stdout.split()[1:]


def _orch(session, ticket):
    run = OrchestrationRun(
        run_code=f"orch-{ticket.external_id}",
        ticket_id=ticket.id,
        workspace_id=ticket.workspace_id,
        status=OrchestrationRunStatus.RUNNING,
    )
    session.add(run)
    session.commit()
    session.refresh(run)
    return run


def _ticket_with_work(session, workspace, repo, milestone, external_id, **fields):
    """A child ticket whose branch carries one commit past the integration branch."""
    ticket = _child(session, workspace, milestone, external_id, **fields)
    target = resolve_target_branch(session, ticket, workspace, repo_root=repo)
    git(repo, "branch", ticket.branch, target)
    tip = _commit_on(repo, ticket.branch, f"{external_id}.txt", "work\n")
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
    _commit_on(repo, ticket.branch, "solo.txt", "solo\n")
    main_before = _sha(repo, "main")

    result = land_ticket(session, ticket, workspace)

    assert result.ok and result.skipped is LandSkip.BASE_TARGET
    assert _sha(repo, "main") == main_before, "main is never moved locally"


def test_a_conflict_is_returned_with_its_files_and_nothing_is_written(
    session, workspace, repo, milestone
):
    ticket, target, _ = _ticket_with_work(session, workspace, repo, milestone, "lg-l-6")
    _commit_on(repo, ticket.branch, "shared.txt", "ticket\n")
    _commit_on(repo, target, "shared.txt", "sibling\n")
    before = _sha(repo, target)

    result = land_ticket(session, ticket, workspace)

    assert not result.ok and result.conflicted
    assert result.conflicted_files == ("shared.txt",)
    assert _sha(repo, target) == before
    assert ticket.landed_sha == ""


# --- at the terminal stage ---------------------------------------------------


def test_completion_lands_the_work_and_marks_the_ticket_done(session, workspace, repo, milestone):
    ticket, target, tip = _ticket_with_work(session, workspace, repo, milestone, "lg-l-7")
    orch = _orch(session, ticket)

    OrchestrationCallbackService(session).complete_orchestration(
        orch, ticket, status=OrchestrationRunStatus.SUCCEEDED
    )

    session.refresh(ticket)
    session.refresh(orch)
    assert orch.status == OrchestrationRunStatus.SUCCEEDED
    assert ticket.landed_branch == target
    assert tip in _parents(repo, ticket.landed_sha)


def test_landing_runs_once_per_completion(session, workspace, repo, milestone):
    ticket, target, _ = _ticket_with_work(session, workspace, repo, milestone, "lg-l-8")
    service = OrchestrationCallbackService(session)

    service.complete_orchestration(
        _orch(session, ticket), ticket, status=OrchestrationRunStatus.SUCCEEDED
    )
    landed = _sha(repo, target)
    service.complete_orchestration(
        _orch(session, ticket), ticket, status=OrchestrationRunStatus.SUCCEEDED
    )

    assert _sha(repo, target) == landed
    assert len(git(repo, "rev-list", "--merges", target).stdout.split()) == 1


def test_a_conflict_blocks_the_ticket_naming_the_files(session, workspace, repo, milestone):
    ticket, target, _ = _ticket_with_work(session, workspace, repo, milestone, "lg-l-9")
    _commit_on(repo, ticket.branch, "shared.txt", "ticket\n")
    _commit_on(repo, target, "shared.txt", "sibling\n")
    orch = _orch(session, ticket)

    OrchestrationCallbackService(session).complete_orchestration(
        orch, ticket, status=OrchestrationRunStatus.SUCCEEDED
    )

    session.refresh(ticket)
    session.refresh(orch)
    assert ticket.state == TicketState.BLOCKED
    assert orch.status == OrchestrationRunStatus.BLOCKED
    assert "shared.txt" in ticket.blocking_issues
    assert ticket.landed_sha == ""


def test_a_conflict_with_auto_resolve_hands_over_to_the_resolver(
    session, workspace, repo, milestone
):
    ticket, target, _ = _ticket_with_work(
        session,
        workspace,
        repo,
        milestone,
        "lg-l-10",
        git_automation_json=json.dumps({"auto_resolve_conflicts": True}),
        workflow_stage_key="implement",
    )
    # The ticket's own worktree, where the resolver will work.
    run = make_run(session, workspace, ticket, "r1")
    tree = resolve_execution_root(session, run, ticket, workspace)
    (tree / "shared.txt").write_text("ticket\n")
    git(tree, "add", "-A")
    git(tree, "commit", "-q", "-m", "ticket side")
    _commit_on(repo, target, "shared.txt", "sibling\n")
    orch = _orch(session, ticket)

    with mock.patch(
        "loregarden.services.conflict_resolution._dispatch_resolver", return_value=True
    ) as dispatch:
        OrchestrationCallbackService(session).complete_orchestration(
            orch, ticket, status=OrchestrationRunStatus.SUCCEEDED
        )

    assert dispatch.called
    session.refresh(ticket)
    session.refresh(orch)
    assert ticket.state != TicketState.DONE
    assert ticket.state != TicketState.BLOCKED
    assert orch.status == OrchestrationRunStatus.FAILED
    assert "resolver dispatched" in (orch.error_message or "")
    assert "shared.txt" in git(tree, "diff", "--name-only", "--diff-filter=U").stdout


def test_a_git_failure_blocks_with_its_text(session, workspace, repo, milestone):
    ticket, target, _ = _ticket_with_work(session, workspace, repo, milestone, "lg-l-11")
    orch = _orch(session, ticket)

    from loregarden.services import land_ticket as module

    real = module.run_git

    def broken(args, **kwargs):
        if args and args[0] == "commit-tree":
            return subprocess.CompletedProcess(
                args, 128, stdout="", stderr="fatal: object store is on fire"
            )
        return real(args, **kwargs)

    with mock.patch.object(module, "run_git", side_effect=broken):
        OrchestrationCallbackService(session).complete_orchestration(
            orch, ticket, status=OrchestrationRunStatus.SUCCEEDED
        )

    session.refresh(ticket)
    assert ticket.state == TicketState.BLOCKED
    assert "on fire" in ticket.blocking_issues
