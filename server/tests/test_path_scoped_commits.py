"""Stage commits are scoped to the ticket's own work (Cat D).

The orchestrator used to `git add -A`, so anything sitting uncommitted in the
workspace — a human's parallel edits, another ticket's half-finished work — was
swept into whichever ticket committed next.
"""

import subprocess

import pytest
from loregarden.models.domain import Ticket, WorkItemType, Workspace
from loregarden.services.git_commit_push_service import commit_paths, working_tree_paths
from sqlmodel import Session, SQLModel, create_engine


@pytest.fixture()
def session_and_ticket(tmp_path, git_repo):
    engine = create_engine(f"sqlite:///{tmp_path / 'db.sqlite'}")
    SQLModel.metadata.create_all(engine)
    session = Session(engine)
    workspace = Workspace(id="ws", slug="ws", name="WS", repo_path=str(git_repo))
    ticket = Ticket(
        id="t1",
        external_id="42-scoped",
        workspace_id="ws",
        title="Scoped",
        work_item_type=WorkItemType.TASK,
    )
    session.add(workspace)
    session.add(ticket)
    session.commit()
    return session, ticket


def _committed_files(repo) -> set[str]:
    out = subprocess.run(
        ["git", "show", "--name-only", "--format=", "HEAD"],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
    )
    return {line for line in out.stdout.split("\n") if line}


def test_unrelated_work_is_not_swept_into_the_commit(session_and_ticket, git_repo):
    session, ticket = session_and_ticket
    (git_repo / "ticket_file.py").write_text("ticket work\n")
    (git_repo / "someone_elses_work.py").write_text("do not commit me\n")

    assert commit_paths(session, ticket, "scoped commit", ["ticket_file.py"]) is True

    committed = _committed_files(git_repo)
    assert committed == {"ticket_file.py"}
    # The bystander is still sitting in the working tree, uncommitted.
    assert "someone_elses_work.py" in working_tree_paths(git_repo)


def test_untracked_files_are_included(session_and_ticket, git_repo):
    """A new file the stage created is its work, not a bystander."""
    session, ticket = session_and_ticket
    (git_repo / "brand_new.py").write_text("new\n")
    assert commit_paths(session, ticket, "add new", ["brand_new.py"]) is True
    assert "brand_new.py" in _committed_files(git_repo)


def test_no_paths_means_no_commit(session_and_ticket, git_repo):
    session, ticket = session_and_ticket
    (git_repo / "bystander.py").write_text("x\n")
    assert commit_paths(session, ticket, "nothing", []) is False
    assert "bystander.py" in working_tree_paths(git_repo)


def test_paths_no_longer_dirty_are_dropped(session_and_ticket, git_repo):
    """A recorded path may have been committed or reverted since; a pathspec
    matching nothing makes `git add` fail rather than no-op."""
    session, ticket = session_and_ticket
    assert commit_paths(session, ticket, "stale", ["never_existed.py"]) is False


def test_working_tree_paths_reports_untracked_and_modified(git_repo):
    (git_repo / "seed.txt").write_text("changed\n")
    (git_repo / "fresh.txt").write_text("fresh\n")
    assert working_tree_paths(git_repo) == {"seed.txt", "fresh.txt"}


def test_working_tree_paths_handles_paths_with_spaces(git_repo):
    """Quoted paths from porcelain would not round-trip back into `git add`."""
    (git_repo / "a file with spaces.txt").write_text("x\n")
    assert "a file with spaces.txt" in working_tree_paths(git_repo)
