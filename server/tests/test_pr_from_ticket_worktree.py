"""Opening a PR publishes the ticket's worktree, not the shared checkout.

Once stages run in their own tree, the shared checkout is not on the ticket's
branch and holds none of its commits. Pushing from there publishes an empty or
wrong branch, and `gh pr create` opens a PR for it.
"""

import subprocess
from pathlib import Path
from unittest import mock

import pytest
from loregarden.models.domain import AgentRun, RunStatus, Ticket, Workspace
from loregarden.services.git_commit_push_service import commit_and_push_ticket_branch
from loregarden.services.github_pr_service import create_ticket_pull_request
from loregarden.services.ticket_worktree import resolve_execution_root
from sqlmodel import Session

BRANCH = "loregarden/lg-1-add-the-thing"


@pytest.fixture(name="session")
def session_fixture(isolated_db):
    with Session(isolated_db) as session:
        yield session


def _git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


@pytest.fixture(name="repo")
def repo_fixture(tmp_path):
    """A checkout with a real bare `origin` to push to."""
    remote = tmp_path / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(remote)], check=True)

    root = tmp_path / "project"
    root.mkdir()
    _git(root, "init", "-q", "-b", "main")
    _git(root, "config", "user.email", "t@example.com")
    _git(root, "config", "user.name", "Test")
    _git(root, "remote", "add", "origin", str(remote))
    (root / "seed.txt").write_text("seed\n")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "seed")
    _git(root, "push", "-q", "-u", "origin", "main")
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
        branch=BRANCH,
    )
    session.add(ticket)
    session.commit()
    session.refresh(ticket)
    return ticket


def _worktree_with_a_commit(session, workspace, ticket):
    run = AgentRun(
        run_code="r1",
        ticket_id=ticket.id,
        workspace_id=workspace.id,
        agent_id="backend_implementer",
        status=RunStatus.RUNNING,
    )
    session.add(run)
    session.commit()
    session.refresh(run)
    root = resolve_execution_root(session, run, ticket, workspace)
    (root / "from-the-worktree.txt").write_text("work\n")
    return root


def _remote_files(repo, branch):
    listing = subprocess.run(
        ["git", "ls-tree", "--name-only", "-r", f"origin/{branch}"],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
    )
    return listing.stdout.split()


def test_open_pr_pushes_the_worktree_branch_and_runs_gh_there(session, workspace, ticket, repo):
    root = _worktree_with_a_commit(session, workspace, ticket)
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "stage work")

    completed = subprocess.CompletedProcess(
        args=["gh"], returncode=0, stdout="https://github.com/acme/proj/pull/7\n", stderr=""
    )
    with mock.patch("loregarden.services.github_pr_service.run_gh", return_value=completed) as gh:
        result = create_ticket_pull_request(session, ticket)

    assert result["number"] == "7"
    # `gh` resolves its repo through git, so where it runs decides which repo
    # and which branch the PR is opened for.
    assert Path(gh.call_args.kwargs["cwd"]) == root
    _git(repo, "fetch", "-q", "origin")
    assert "from-the-worktree.txt" in _remote_files(repo, BRANCH)


def test_open_pr_fails_loudly_when_the_branch_cannot_be_pushed(session, workspace, ticket, repo):
    _worktree_with_a_commit(session, workspace, ticket)
    _git(repo, "remote", "set-url", "origin", str(repo.parent / "does-not-exist.git"))

    with pytest.raises(ValueError):
        create_ticket_pull_request(session, ticket)


def test_operator_commit_and_push_takes_the_worktree_not_the_shared_tree(
    session, workspace, ticket, repo
):
    _worktree_with_a_commit(session, workspace, ticket)
    # Something unrelated sitting in the shared checkout must not be swept into
    # the ticket's commit.
    (repo / "unrelated.txt").write_text("someone else's work\n")

    result = commit_and_push_ticket_branch(session, ticket)

    assert result == {"branch": BRANCH, "committed": True, "pushed": True}
    _git(repo, "fetch", "-q", "origin")
    files = _remote_files(repo, BRANCH)
    assert "from-the-worktree.txt" in files
    assert "unrelated.txt" not in files
