"""Git automation: what a finished run is allowed to do with its work.

The behaviour worth pinning is the chain — each step gated on the one before —
and that a queued run's work goes to its own worktree branch rather than the
shared checkout.
"""

import subprocess
from pathlib import Path

import pytest
from loregarden.models.domain import AgentRun, RunStatus, Ticket, Workspace
from loregarden.services.git_automation import run_git_automation
from loregarden.services.git_automation_config import (
    enabled_steps,
    parse_override,
    resolve_git_automation,
    serialize_override,
)
from loregarden.services.orchestration_profile import GitAutomationConfig
from loregarden.services.worktree_service import WorktreeService
from sqlmodel import Session


@pytest.fixture(name="session")
def session_fixture(isolated_db):
    with Session(isolated_db) as session:
        yield session


@pytest.fixture(name="repo")
def repo_fixture(tmp_path):
    """A throwaway repo with a `main` branch and one commit."""
    root = tmp_path / "project"
    root.mkdir()

    def git(*args):
        subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)

    git("init", "-q", "-b", "main")
    git("config", "user.email", "t@example.com")
    git("config", "user.name", "Test")
    (root / "seed.txt").write_text("seed\n")
    git("add", "-A")
    git("commit", "-q", "-m", "seed")
    return root


@pytest.fixture(name="workspace")
def workspace_fixture(session, repo):
    ws = Workspace(slug="proj", name="proj", repo_path=str(repo))
    session.add(ws)
    session.commit()
    session.refresh(ws)
    return ws


@pytest.fixture(name="ticket")
def ticket_fixture(session, workspace):
    ticket = Ticket(
        external_id="LG-1",
        workspace_id=workspace.id,
        title="Add the thing",
        branch="lg-1-add-the-thing",
    )
    session.add(ticket)
    session.commit()
    session.refresh(ticket)
    return ticket


@pytest.fixture(name="run")
def run_fixture(session, workspace, ticket):
    run = AgentRun(
        run_code="run_abc",
        ticket_id=ticket.id,
        workspace_id=workspace.id,
        agent_id="backend_implementer",
        status=RunStatus.RUNNING,
    )
    session.add(run)
    session.commit()
    session.refresh(run)
    return run


# ---- config resolution -------------------------------------------------


def test_steps_stop_at_the_first_switch_that_is_off():
    """The steps are a chain, not four independent choices: pushing an
    uncommitted tree does nothing and a PR needs a pushed branch."""
    assert enabled_steps(GitAutomationConfig(commit=True, push=True, open_pr=True)) == [
        "commit",
        "push",
        "open_pr",
    ]
    # open_pr on with push off runs neither — the config is incoherent, and
    # doing half of it would push work nobody asked to publish.
    assert enabled_steps(GitAutomationConfig(commit=True, open_pr=True)) == ["commit"]
    assert enabled_steps(GitAutomationConfig()) == []


def test_a_ticket_overrides_only_the_keys_it_names(session, workspace, ticket):
    ticket.git_automation_json = serialize_override({"auto_merge": False})
    session.add(ticket)
    session.commit()

    config = resolve_git_automation(workspace, ticket)

    assert config.auto_merge is False
    # Everything else still comes from the profile, so a workspace that later
    # turns on PRs reaches this ticket too.
    assert config.worktree is GitAutomationConfig().worktree


def test_no_override_means_inherit_not_everything_off(session, workspace, ticket):
    assert ticket.git_automation_json == ""
    assert resolve_git_automation(workspace, ticket) == resolve_git_automation(workspace)


def test_unknown_and_unparseable_overrides_are_ignored():
    assert parse_override('{"commit": true, "launch_missiles": true}') == {"commit": True}
    assert parse_override("not json") == {}
    assert parse_override("[1,2]") == {}
    assert serialize_override({"nope": 1}) == ""


# ---- the pipeline ------------------------------------------------------


def test_commit_off_does_nothing_at_all(session, run, ticket):
    result = run_git_automation(session, run, ticket, GitAutomationConfig(commit=False))

    assert result.steps == []
    assert result.ok


def test_commit_records_the_ticket_in_the_message(session, run, ticket, repo):
    (repo / "new.txt").write_text("work\n")

    result = run_git_automation(session, run, ticket, GitAutomationConfig(commit=True))

    assert result.ok, result.as_dict()
    log = subprocess.run(
        ["git", "log", "-1", "--pretty=%s"],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
    )
    assert log.stdout.strip() == "LG-1: Add the thing"


def test_an_empty_tree_is_not_a_failure(session, run, ticket):
    """A stage that only read the codebase has nothing of its own to commit,
    and must not block a PR that should still open."""
    result = run_git_automation(session, run, ticket, GitAutomationConfig(commit=True))

    assert result.ok
    assert result.steps[0].detail == "nothing to commit"


def test_a_failed_push_stops_the_pipeline_before_the_pr(session, run, ticket, repo):
    (repo / "new.txt").write_text("work\n")

    # No `origin` configured, so the push cannot succeed.
    result = run_git_automation(
        session, run, ticket, GitAutomationConfig(commit=True, push=True, open_pr=True)
    )

    assert not result.ok
    assert result.failure.step == "push"
    # The PR step never ran: publishing a branch that is not on the remote
    # would open a PR against commits nobody else can see.
    assert [s.step for s in result.steps] == ["commit", "push"]


# ---- worktrees ---------------------------------------------------------


def test_a_worktree_gets_its_own_branch_off_the_parent(session, workspace, run, repo):
    service = WorktreeService(session, repo_path=str(repo))

    worktree = service.create_worktree(
        workspace_id=workspace.id,
        agent_run_id=run.id,
        parent_branch="main",
        branch="lg-1-add-the-thing",
    )

    assert worktree is not None
    assert worktree.branch == "lg-1-add-the-thing"
    head = subprocess.run(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"],
        cwd=worktree.worktree_path,
        capture_output=True,
        text=True,
        check=True,
    )
    # Not `main`: checking the parent out directly is what git refuses when the
    # root already has it, and it put the run's commits straight onto main.
    assert head.stdout.strip() == "lg-1-add-the-thing"


def test_the_run_commits_in_its_worktree_not_the_workspace(session, workspace, ticket, run, repo):
    service = WorktreeService(session, repo_path=str(repo))
    worktree = service.create_worktree(
        workspace_id=workspace.id,
        agent_run_id=run.id,
        parent_branch="main",
        branch="lg-1-add-the-thing",
    )
    run.worktree_id = worktree.id
    session.add(run)
    session.commit()

    (Path(worktree.worktree_path) / "in-worktree.txt").write_text("work\n")

    result = run_git_automation(session, run, ticket, GitAutomationConfig(commit=True))

    assert result.ok, result.as_dict()
    # The shared checkout is untouched — the whole point of the isolation.
    assert not (repo / "in-worktree.txt").exists()
    files = subprocess.run(
        ["git", "show", "--name-only", "--pretty=", "HEAD"],
        cwd=worktree.worktree_path,
        capture_output=True,
        text=True,
        check=True,
    )
    assert "in-worktree.txt" in files.stdout


def test_a_ticket_override_round_trips_through_the_patch_field(session, workspace, ticket):
    """The override is only useful if something can set it."""
    from loregarden.models.domain import UpdateTicketRequest
    from loregarden.services.ticket_manual_edit import _apply_operator_edits

    _apply_operator_edits(ticket, UpdateTicketRequest(git_automation={"commit": True}))
    session.add(ticket)
    session.commit()

    assert resolve_git_automation(workspace, ticket).commit is True

    # {} restores inheritance, which is not the same as everything off.
    _apply_operator_edits(ticket, UpdateTicketRequest(git_automation={}))
    session.add(ticket)
    session.commit()

    assert ticket.git_automation_json == ""
    assert resolve_git_automation(workspace, ticket) == resolve_git_automation(workspace)
