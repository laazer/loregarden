"""A tree's finished PR is a member's only when it carries the member's work.

GitHub answers `gh pr view <branch>` with the branch's latest PR, finished or
not. On 2026-11-17 lg-code-knowledge-1055's PR tab showed #894, the tree's PR,
merged at 17:47 UTC — four hours before 1055 passed its gate and with none of
its commits. An OPEN tree PR is still where the work ships next; a MERGED or
CLOSED one belongs to the member only if its head holds the member's commits.
"""

from __future__ import annotations

import json
import subprocess
from unittest import mock

import pytest
from loregarden.models.domain import PullRequestLookup, PullRequestState, Ticket, Workspace
from loregarden.services import ticket_pull_request as tpr
from loregarden.services.branch_names import resolve_ticket_branch
from loregarden.services.publish_ref import publish_ref_for
from loregarden.services.ticket_pull_request import ticket_pull_request
from sqlmodel import Session
from tests.worktree_helpers import commit_on, git, make_repo

INTEGRATION = "integration/LG-1"
PUBLISH = publish_ref_for(INTEGRATION)


@pytest.fixture(name="session")
def session_fixture(isolated_db):
    with Session(isolated_db) as session:
        yield session


@pytest.fixture(name="repo")
def repo_fixture(tmp_path):
    repo = make_repo(tmp_path)
    git(repo, "branch", INTEGRATION)
    return repo


@pytest.fixture(name="workspace")
def workspace_fixture(session, repo):
    ws = Workspace(slug="proj", name="proj", repo_path=str(repo))
    session.add(ws)
    session.commit()
    session.refresh(ws)
    return ws


@pytest.fixture(name="child")
def child_fixture(session, workspace, repo):
    """A tree member with one commit of its own, cut from the integration branch."""
    root = Ticket(external_id="LG-1", workspace_id=workspace.id, title="Root")
    session.add(root)
    session.commit()
    child = Ticket(
        external_id="LG-2", workspace_id=workspace.id, title="Child", parent_ticket_id=root.id
    )
    session.add(child)
    session.commit()
    session.refresh(child)
    git(repo, "branch", resolve_ticket_branch(child), INTEGRATION)
    commit_on(repo, resolve_ticket_branch(child), "child.txt", "the child's work\n")
    return child


def _tip(repo, ref: str) -> str:
    return git(repo, "rev-parse", ref).stdout.strip()


def _land(repo, child: Ticket) -> str:
    """Merge the child into the integration branch, as a landing does."""
    tree = repo.parent / "tmp-land"
    git(repo, "worktree", "add", "-q", str(tree), INTEGRATION)
    git(tree, "merge", "-q", "--no-ff", "-m", "Land LG-2", resolve_ticket_branch(child))
    sha = _tip(tree, "HEAD")
    git(repo, "worktree", "remove", "--force", str(tree))
    return sha


def _tree_pr(state: str, head_sha: str) -> dict:
    return {
        "number": 894,
        "url": "https://github.com/o/r/pull/894",
        "title": "LG-1: the tree",
        "state": state,
        "isDraft": False,
        "baseRefName": "main",
        "headRefName": PUBLISH,
        "headRefOid": head_sha,
        "statusCheckRollup": [],
    }


def _gh(pr: dict):
    def view(args, **_kwargs):
        if args[2] == PUBLISH:
            return subprocess.CompletedProcess(args, 0, stdout=json.dumps(pr), stderr="")
        none = f'no pull requests found for branch "{args[2]}"'
        return subprocess.CompletedProcess(args, 1, stdout="", stderr=none)

    return mock.patch.object(tpr, "run_gh", side_effect=view)


@pytest.mark.parametrize("state", ["MERGED", "CLOSED"])
def test_a_finished_tree_pr_without_the_members_work_is_not_its_pr(
    session, workspace, repo, child, state
):
    """The 1055 shape: the tree's PR finished before the member reached the tree."""
    with _gh(_tree_pr(state, _tip(repo, INTEGRATION))):
        result = ticket_pull_request(session, child, workspace)

    assert result.lookup is PullRequestLookup.NONE, result.pull_request
    assert result.pull_request is None
    assert result.ships_with == INTEGRATION, "the panel still says it ships in the tree's PR"
    earlier = result.earlier_tree_pull_request
    assert earlier is not None, "the finished PR is named, so the panel can say what happened"
    assert (earlier.number, earlier.state) == (894, PullRequestState(state.lower()))


def test_a_merged_tree_pr_that_carried_the_members_work_is_still_its_pr(
    session, workspace, repo, child
):
    landed = _land(repo, child)

    with _gh(_tree_pr("MERGED", landed)):
        result = ticket_pull_request(session, child, workspace)

    assert result.lookup is PullRequestLookup.FOUND
    assert result.pull_request is not None and result.pull_request.number == 894
    assert result.is_tree_pull_request is True
    assert result.earlier_tree_pull_request is None


def test_the_landing_commit_answers_once_the_members_branch_is_gone(
    session, workspace, repo, child
):
    """After a land the member's branch may be deleted; its landing sha remains."""
    child.landed_sha = _land(repo, child)
    session.add(child)
    session.commit()
    git(repo, "branch", "-D", resolve_ticket_branch(child))

    with _gh(_tree_pr("MERGED", child.landed_sha)):
        result = ticket_pull_request(session, child, workspace)

    assert result.lookup is PullRequestLookup.FOUND


def test_an_open_tree_pr_is_where_the_work_ships_next_contained_or_not(
    session, workspace, repo, child
):
    with _gh(_tree_pr("OPEN", _tip(repo, INTEGRATION))):
        result = ticket_pull_request(session, child, workspace)

    assert result.lookup is PullRequestLookup.FOUND
    assert result.is_tree_pull_request is True


def test_a_head_not_in_this_repository_keeps_the_pr_and_says_why(session, workspace, repo, child):
    """Unanswerable here (the head was never fetched): keep the old answer, loudly."""
    with (
        _gh(_tree_pr("MERGED", "0" * 40)),
        mock.patch.object(tpr.logger, "warning") as warned,
    ):
        result = ticket_pull_request(session, child, workspace)

    assert result.lookup is PullRequestLookup.FOUND
    assert warned.called, "keeping an unverified PR must be reported, not silent"
    assert "LG-2" in warned.call_args.args
