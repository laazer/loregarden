"""The git boundary a run starts from.

Two properties matter here and neither is obvious from the happy path: a run in
a worktree must record *that* checkout rather than the shared one it was
dispatched from, and an unreadable repository must produce an empty boundary
instead of an exception. The second is what keeps this a diagnostic rather than
a new way for a dispatch to die.
"""

from pathlib import Path

import pytest
from loregarden.models.domain import AgentRun, RunStatus, Workspace
from loregarden.services.git_boundary import (
    boundary_of_run,
    current_branch,
    read_boundary,
    stamp_run_boundary,
)
from loregarden.services.ticket_worktree import resolve_execution_root
from sqlmodel import Session
from tests.worktree_helpers import git, make_repo, make_ticket


@pytest.fixture(name="session")
def session_fixture(isolated_db):
    with Session(isolated_db) as session:
        yield session


@pytest.fixture(name="repo")
def repo_fixture(tmp_path):
    return make_repo(tmp_path)


@pytest.fixture(name="workspace")
def workspace_fixture(session, repo):
    ws = Workspace(slug="proj", name="proj", repo_path=str(repo))
    session.add(ws)
    session.commit()
    session.refresh(ws)
    return ws


@pytest.fixture(name="ticket")
def ticket_fixture(session, workspace):
    return make_ticket(session, workspace)


def _run(session, workspace, ticket) -> AgentRun:
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
    return run


def test_reads_the_checkout_branch_sha_and_dirty_paths(repo):
    (repo / "scratch.txt").write_text("uncommitted\n")

    boundary = read_boundary(repo)

    assert boundary.repo_path == str(repo)
    assert boundary.branch == "main"
    assert boundary.head_sha == git(repo, "rev-parse", "HEAD").stdout.strip()
    assert boundary.dirty_paths == ["scratch.txt"]
    assert boundary.is_recorded


def test_a_clean_tree_has_no_dirty_paths(repo):
    assert read_boundary(repo).dirty_paths == []


def test_caller_supplied_dirty_paths_are_used_as_given(repo):
    """The dispatch path already computed these to bracket the run; recomputing
    them would be a second git call and a second answer."""
    (repo / "ignored-by-caller.txt").write_text("x\n")

    boundary = read_boundary(repo, dirty_paths={"b.txt", "a.txt"})

    assert boundary.dirty_paths == ["a.txt", "b.txt"]


def test_detached_head_records_no_branch(repo):
    git(repo, "checkout", "-q", "--detach")

    assert current_branch(repo) == ""
    assert read_boundary(repo).branch == ""
    assert read_boundary(repo).is_recorded, "a detached head still has a commit"


def test_a_directory_that_is_not_a_repo_yields_an_empty_boundary(tmp_path):
    plain = tmp_path / "not-a-repo"
    plain.mkdir()

    boundary = read_boundary(plain)

    assert boundary.branch == ""
    assert boundary.head_sha == ""
    assert not boundary.is_recorded


def test_a_missing_directory_yields_an_empty_boundary(tmp_path):
    boundary = read_boundary(tmp_path / "gone")

    assert boundary == type(boundary)()
    assert not boundary.is_recorded


def test_a_repo_with_no_commits_is_unrecorded(tmp_path):
    """`is_recorded` keys on the sha, so a fresh `git init` reads as unknown —
    there is no commit for a later stage to compare against."""
    fresh = tmp_path / "fresh"
    fresh.mkdir()
    git(fresh, "init", "-q", "-b", "main")

    boundary = read_boundary(fresh)

    assert boundary.branch == "main"
    assert boundary.head_sha == ""
    assert not boundary.is_recorded


def test_a_run_in_a_worktree_records_the_worktree_not_the_workspace_root(
    session, workspace, ticket, repo
):
    run = _run(session, workspace, ticket)

    execution_root = resolve_execution_root(session, run, ticket, workspace)
    boundary = read_boundary(execution_root)

    assert execution_root != repo, "expected a worktree, got the shared checkout"
    assert boundary.repo_path == str(execution_root)
    assert boundary.branch == ticket.branch
    assert boundary.branch != "main"


def test_stamping_round_trips_through_the_run(session, workspace, ticket, repo):
    run = _run(session, workspace, ticket)
    (repo / "scratch.txt").write_text("x\n")
    boundary = read_boundary(repo)

    stamp_run_boundary(session, run, boundary)
    session.refresh(run)

    assert boundary_of_run(run) == boundary
    assert run.start_repo_path == str(repo)
    assert run.start_dirty_paths_json == '["scratch.txt"]'


def test_a_run_stamped_before_the_columns_existed_reads_as_unrecorded(session, workspace, ticket):
    run = _run(session, workspace, ticket)

    assert not boundary_of_run(run).is_recorded


def test_an_unreadable_repo_stamps_an_empty_boundary_rather_than_raising(
    session, workspace, ticket, tmp_path
):
    run = _run(session, workspace, ticket)

    stamp_run_boundary(session, run, read_boundary(tmp_path / "gone"))
    session.refresh(run)

    assert not boundary_of_run(run).is_recorded
    assert run.start_dirty_paths_json == "[]"


def test_boundary_path_is_the_resolved_string_not_a_path_object(repo):
    """It round-trips through a TEXT column, so the model must already hold the
    string form — a Path here would stringify differently on the way back."""
    assert isinstance(read_boundary(Path(repo)).repo_path, str)
