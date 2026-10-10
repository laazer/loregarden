"""The PR tab's "Merge and clean up": merge only what GitHub says can ship, then tidy.

GitHub is faked at `run_gh`; git is real, so the cleanup is checked against a
real worktree, a real local branch, and what the fake was asked to delete.
"""

from __future__ import annotations

import json
import subprocess
from unittest import mock

import pytest
from loregarden.mcp.tool_ids import AUTO_APPROVED_MCP_TOOLS, ORCHESTRATED_DENIED_MCP_TOOLS, McpTool
from loregarden.mcp.tools import execute_tool, normalize_tool_arguments
from loregarden.models.domain import (
    AgentRun,
    RunStatus,
    Ticket,
    Workspace,
    Worktree,
    WorktreeState,
)
from loregarden.services import pull_request_merge, ticket_pull_request
from loregarden.services.pull_request_merge import MergeFailed, MergeRefused, merge_and_clean_up
from sqlmodel import Session, select
from tests.worktree_helpers import git, make_repo

BRANCH = "loregarden/lg-1"
SHA = "a" * 40


@pytest.fixture(name="session")
def session_fixture(isolated_db):
    with Session(isolated_db) as session:
        yield session


@pytest.fixture(name="repo")
def repo_fixture(tmp_path):
    repo = make_repo(tmp_path)
    git(repo, "branch", BRANCH)
    return repo


@pytest.fixture(name="workspace")
def workspace_fixture(session, repo):
    ws = Workspace(slug="merge", name="merge", repo_path=str(repo))
    session.add(ws)
    session.commit()
    session.refresh(ws)
    return ws


@pytest.fixture(name="ticket")
def ticket_fixture(session, workspace):
    ticket = Ticket(external_id="lg-1", workspace_id=workspace.id, title="t", branch=BRANCH)
    session.add(ticket)
    session.commit()
    session.refresh(ticket)
    return ticket


def _pr(**overrides):
    payload = {
        "number": 7,
        "url": "https://github.com/o/r/pull/7",
        "title": "t",
        "state": "OPEN",
        "baseRefName": "main",
        "headRefName": BRANCH,
        "headRefOid": SHA,
        "mergeStateStatus": "CLEAN",
        "statusCheckRollup": [],
    }
    return {**payload, **overrides}


class FakeGitHub:
    """Answers the four `gh` calls the merge makes, and records them."""

    def __init__(self, pr: dict, *, merge_exit: int = 0, state_after: str = "MERGED"):
        self.pr = pr
        self.merge_exit = merge_exit
        self.state_after = state_after
        self.calls: list[list[str]] = []

    def __call__(self, args, *, cwd, timeout=None, gh_token=None):
        self.calls.append(list(args))
        if args[:2] == ["pr", "view"] and "state,mergeCommit" in args:
            out = {"state": self.state_after, "mergeCommit": {"oid": "m" * 40}}
            return subprocess.CompletedProcess(args, 0, json.dumps(out), "")
        if args[:2] == ["pr", "view"]:
            return subprocess.CompletedProcess(args, 0, json.dumps(self.pr), "")
        if args[:2] == ["pr", "merge"]:
            return subprocess.CompletedProcess(args, self.merge_exit, "", "merge refused by test")
        if args[0] == "api" and args[1].endswith("/commits?per_page=100"):
            return subprocess.CompletedProcess(args, 0, "20a44d8c test: unsigned\n", "")
        if args[:3] == ["api", "-X", "DELETE"]:
            return subprocess.CompletedProcess(args, 0, "", "")
        raise AssertionError(f"unexpected gh call {args}")

    def did(self, *prefix: str) -> bool:
        return any(call[: len(prefix)] == list(prefix) for call in self.calls)


@pytest.fixture(name="github")
def github_fixture():
    def install(fake: FakeGitHub):
        stack = [
            mock.patch.object(ticket_pull_request, "run_gh", fake),
            mock.patch.object(pull_request_merge, "run_gh", fake),
        ]
        for patcher in stack:
            patcher.start()
        return fake

    yield install
    mock.patch.stopall()


def test_merges_pinned_to_the_head_it_was_shown_then_cleans_up(
    session, workspace, ticket, repo, github
):
    tree = repo.parent / "lg-1-tree"
    git(repo, "worktree", "add", "-q", str(tree), BRANCH)
    run = AgentRun(
        run_code="r1",
        ticket_id=ticket.id,
        workspace_id=workspace.id,
        agent_id="backend_implementer",
        status=RunStatus.SUCCEEDED,
    )
    session.add(run)
    session.commit()
    session.add(
        Worktree(
            agent_run_id=run.id,
            workspace_id=workspace.id,
            ticket_id=ticket.id,
            branch=BRANCH,
            worktree_path=str(tree),
            state=WorktreeState.ACTIVE,
        )
    )
    session.commit()
    fake = github(FakeGitHub(_pr()))

    result = merge_and_clean_up(session, ticket, workspace, number=7, head_sha=SHA)

    assert ["pr", "merge", "7", "--squash", "--match-head-commit", SHA] in fake.calls
    assert result.merge_commit == "m" * 40
    assert all(step.ok for step in result.steps), result.steps
    assert not tree.exists()
    assert git(repo, "branch", "--list", BRANCH).stdout.strip() == ""
    assert fake.did("api", "-X", "DELETE", f"repos/{{owner}}/{{repo}}/git/refs/heads/{BRANCH}")
    row = session.exec(select(Worktree)).one()
    assert row.state is WorktreeState.CLEANUP


@pytest.mark.parametrize(
    ("pr", "number", "head", "match"),
    [
        (_pr(mergeStateStatus="BLOCKED"), 7, SHA, "cannot merge yet"),
        (_pr(), 7, "b" * 40, "new commits"),
        (_pr(), 8, SHA, "no longer"),
    ],
)
def test_refuses_without_asking_github_to_merge(
    session, workspace, ticket, github, pr, number, head, match
):
    fake = github(FakeGitHub(pr))
    with pytest.raises(MergeRefused, match=match):
        merge_and_clean_up(session, ticket, workspace, number=number, head_sha=head)
    assert not fake.did("pr", "merge")


def test_a_merge_gh_reports_but_github_does_not_show_is_a_failure(
    session, workspace, ticket, repo, github
):
    github(FakeGitHub(_pr(), state_after="OPEN"))
    with pytest.raises(MergeFailed, match="'OPEN'"):
        merge_and_clean_up(session, ticket, workspace, number=7, head_sha=SHA)
    assert git(repo, "branch", "--list", BRANCH).stdout.strip(), "nothing cleaned up"


def test_a_refused_merge_cleans_up_nothing(session, workspace, ticket, repo, github):
    fake = github(FakeGitHub(_pr(), merge_exit=1))
    with pytest.raises(MergeFailed, match="merge refused by test"):
        merge_and_clean_up(session, ticket, workspace, number=7, head_sha=SHA)
    assert not fake.did("api")
    assert git(repo, "branch", "--list", BRANCH).stdout.strip()


def test_a_worktree_with_uncommitted_work_is_kept_and_said_so(
    session, workspace, ticket, repo, github
):
    tree = repo.parent / "lg-1-tree"
    git(repo, "worktree", "add", "-q", str(tree), BRANCH)
    (tree / "unsaved.txt").write_text("work in progress\n")
    github(FakeGitHub(_pr()))

    result = merge_and_clean_up(session, ticket, workspace, number=7, head_sha=SHA)

    by_step = {step.step: step for step in result.steps}
    assert not by_step[f"worktree {tree}"].ok
    assert "uncommitted" in by_step[f"worktree {tree}"].detail
    assert tree.exists() and (tree / "unsaved.txt").exists()
    assert not by_step["local branch"].ok, "a branch still checked out is not deleted"
    assert by_step["remote branch"].ok


def test_a_tree_roots_pr_is_looked_up_on_its_integration_branch(
    session, workspace, ticket, repo, github
):
    """`publish_tree` publishes a root's integration branch, so its PR lives there."""
    git(repo, "branch", "integration/lg-1")
    session.add(
        Ticket(external_id="lg-2", workspace_id=workspace.id, title="c", parent_ticket_id=ticket.id)
    )
    session.commit()
    fake = github(FakeGitHub(_pr(headRefName="integration/lg-1")))

    found = ticket_pull_request.ticket_pull_request(session, ticket, workspace)

    assert fake.calls[0][:3] == ["pr", "view", "integration/lg-1"]
    assert found.branch == "integration/lg-1"
    assert found.pull_request is not None and found.pull_request.mergeable_now


def test_a_blocked_pr_names_its_unsigned_commits_and_a_clean_one_is_not_asked(
    session, workspace, ticket, github
):
    fake = github(FakeGitHub(_pr(mergeStateStatus="BLOCKED")))
    blocked = ticket_pull_request.ticket_pull_request(session, ticket, workspace).pull_request
    assert blocked is not None and blocked.unsigned_commits == ["20a44d8c test: unsigned"]

    fake.pr = _pr()
    fake.calls.clear()
    clean = ticket_pull_request.ticket_pull_request(session, ticket, workspace).pull_request
    assert clean is not None and clean.unsigned_commits == []
    assert not any(call[0] == "api" for call in fake.calls)


# --- agents: the same merge over MCP --------------------------------------------------


def _mcp(session, arguments, **kwargs):
    name = McpTool.MERGE_PULL_REQUEST.value
    return execute_tool(session, name, normalize_tool_arguments(name, arguments), **kwargs)


def test_an_agent_merges_through_mcp_with_the_same_checks(session, workspace, ticket, github):
    fake = github(FakeGitHub(_pr()))

    result = json.loads(_mcp(session, {"ticket_id": "lg-1", "number": 7, "head_sha": SHA}))

    assert ["pr", "merge", "7", "--squash", "--match-head-commit", SHA] in fake.calls
    assert result["merge_commit"] == "m" * 40
    assert {step["step"] for step in result["steps"]} >= {"local branch", "remote branch"}


def test_an_agent_without_a_head_sha_merges_the_head_github_reports(
    session, workspace, ticket, github
):
    fake = github(FakeGitHub(_pr()))
    _mcp(session, {"ticket_id": "lg-1", "number": "7"})
    assert ["pr", "merge", "7", "--squash", "--match-head-commit", SHA] in fake.calls


def test_an_agent_is_refused_what_a_person_would_be(session, workspace, ticket, github):
    fake = github(FakeGitHub(_pr(mergeStateStatus="BLOCKED")))
    with pytest.raises(ValueError, match="cannot merge yet"):
        _mcp(session, {"ticket_id": "lg-1", "number": 7, "head_sha": SHA})
    assert not fake.did("pr", "merge")


def test_merging_is_gated_and_never_a_pipeline_stages_to_do():
    """Not auto-approved: a gated run asks first. And a stage agent may not merge
    the ticket it was dispatched for."""
    assert McpTool.MERGE_PULL_REQUEST not in AUTO_APPROVED_MCP_TOOLS
    assert McpTool.MERGE_PULL_REQUEST in ORCHESTRATED_DENIED_MCP_TOOLS


def test_a_pipeline_agent_calling_it_is_refused(session, workspace, ticket, github):
    fake = github(FakeGitHub(_pr()))
    with pytest.raises(ValueError):
        _mcp(session, {"ticket_id": "lg-1", "number": 7, "head_sha": SHA}, orchestrated=True)
    assert not fake.did("pr", "merge")
