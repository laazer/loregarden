"""A finished tree's integration branch reaches the base through the publish chain (771)."""

from __future__ import annotations

import json
import subprocess
from unittest import mock

import pytest
from loregarden.models.domain import (
    Ticket,
    TicketState,
    WorkItemType,
    Workspace,
)
from loregarden.services import git_automation
from loregarden.services.land_ticket import land_ticket
from loregarden.services.orchestration import OrchestrationService
from loregarden.services.publish_tree import publish_tree
from loregarden.services.target_branch import resolve_target_branch
from sqlmodel import Session
from tests.worktree_helpers import at_terminal_stage, blocking_text, commit_on, git, make_repo


@pytest.fixture(name="session")
def session_fixture(isolated_db):
    with Session(isolated_db) as session:
        yield session


@pytest.fixture(name="repo")
def repo_fixture(tmp_path):
    repo = make_repo(tmp_path)
    remote = tmp_path / "remote.git"
    git(tmp_path, "init", "-q", "--bare", str(remote))
    git(repo, "remote", "add", "origin", str(remote))
    git(repo, "push", "-q", "-u", "origin", "main")
    return repo


@pytest.fixture(name="workspace")
def workspace_fixture(session, repo):
    ws = Workspace(slug="publish-tree", name="publish", repo_path=str(repo))
    session.add(ws)
    session.commit()
    session.refresh(ws)
    return ws


def _ticket(session, workspace, external_id, *, parent=None, kind=WorkItemType.TASK, **fields):
    ticket = Ticket(
        external_id=external_id,
        workspace_id=workspace.id,
        title=f"Ticket {external_id}",
        branch=f"loregarden/{external_id}",
        parent_ticket_id=parent.id if parent else None,
        state=TicketState.IN_PROGRESS,
        work_item_type=kind,
        **fields,
    )
    session.add(ticket)
    session.commit()
    session.refresh(ticket)
    return ticket


def _sha(cwd, ref="HEAD"):
    return git(cwd, "rev-parse", ref).stdout.strip()


def _remote_sha(repo, branch):
    out = git(repo, "ls-remote", "origin", f"refs/heads/{branch}").stdout.split()
    return out[0] if out else ""


def _landed_tree(session, workspace, repo, policy=None):
    """A milestone with one child whose work has landed on the integration branch."""
    fields = {"git_automation_json": json.dumps(policy)} if policy else {}
    ms = _ticket(session, workspace, "ms", kind=WorkItemType.MILESTONE, **fields)
    child = _ticket(session, workspace, "c1", parent=ms)
    target = resolve_target_branch(session, child, workspace, repo_root=repo)
    git(repo, "branch", child.branch, target)
    commit_on(repo, child.branch, "c1.txt", "child work\n")
    assert land_ticket(session, child, workspace).ok
    return ms, target


PUSH_ONLY = {"commit": True, "push": True, "open_pr": False}


def test_the_root_pushes_its_integration_branch_and_records_the_tip(session, workspace, repo):
    ms, target = _landed_tree(session, workspace, repo, PUSH_ONLY)
    tip = _sha(repo, target)

    outcome = publish_tree(session, ms, workspace)

    assert outcome.ok and not outcome.skipped
    assert _remote_sha(repo, target) == tip
    assert outcome.pr_url == "", "open_pr is off: the PR stays a person's decision"
    session.refresh(ms)
    assert (ms.landed_branch, ms.landed_sha) == (target, tip)


def test_publishing_runs_once_per_tip(session, workspace, repo):
    ms, target = _landed_tree(session, workspace, repo, PUSH_ONLY)
    first = publish_tree(session, ms, workspace)
    assert first.ok and not first.skipped

    with mock.patch.object(git_automation, "_push", wraps=git_automation._push) as push:
        second = publish_tree(session, ms, workspace)

    assert second.ok and second.skipped
    push.assert_not_called()


def test_a_non_root_ticket_publishes_nothing(session, workspace, repo):
    ms = _ticket(session, workspace, "ms", kind=WorkItemType.MILESTONE)
    child = _ticket(session, workspace, "c1", parent=ms)
    outcome = publish_tree(session, child, workspace)
    assert outcome.ok and outcome.skipped and outcome.branch == ""


def test_a_root_finishing_its_workflow_publishes_and_marks_done(session, workspace, repo):
    ms, target = _landed_tree(session, workspace, repo, PUSH_ONLY)
    at_terminal_stage(session, ms)

    OrchestrationService(session).advance_stage(ms)

    session.refresh(ms)
    assert ms.state == TicketState.DONE
    assert _remote_sha(repo, target) == _sha(repo, target)


def test_a_push_failure_blocks_the_root_with_the_reason(session, workspace, repo):
    ms, target = _landed_tree(session, workspace, repo, PUSH_ONLY)
    git(repo, "remote", "set-url", "origin", str(repo.parent / "no-such-remote.git"))
    at_terminal_stage(session, ms)

    OrchestrationService(session).advance_stage(ms)

    session.refresh(ms)
    assert ms.state == TicketState.BLOCKED
    recorded = blocking_text(session, ms)
    assert "push failed" in recorded and "no-such-remote" in recorded, "git's own words"
    assert ms.landed_sha == ""


def test_with_open_pr_on_a_single_pr_is_opened_from_the_integration_branch(
    session, workspace, repo
):
    ms, target = _landed_tree(
        session, workspace, repo, {"commit": True, "push": True, "open_pr": True}
    )
    calls: list[list[str]] = []

    def fake_gh(args, cwd):
        calls.append(args)
        if args[:2] == ["pr", "view"]:
            return subprocess.CompletedProcess(args, 1, stdout="", stderr="no pull requests found")
        return subprocess.CompletedProcess(
            args, 0, stdout="https://github.com/example/repo/pull/1\n", stderr=""
        )

    with mock.patch.object(git_automation, "_gh", side_effect=fake_gh):
        outcome = publish_tree(session, ms, workspace)

    assert outcome.ok
    assert outcome.pr_url == "https://github.com/example/repo/pull/1"
    create = next(c for c in calls if c[:2] == ["pr", "create"])
    assert create[create.index("--head") + 1] == target
    assert create[create.index("--base") + 1] == "main"
    assert "ms" in create[create.index("--title") + 1]
    assert not any(c[:2] == ["pr", "merge"] for c in calls), "auto_merge stays off"
