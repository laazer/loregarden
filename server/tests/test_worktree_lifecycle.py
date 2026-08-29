"""Worktrees are retired when the ticket ends, and after a crash.

The failure this closes is "server restart kills mid-run orchestration": the
row said active, the process that owned it was gone, and nothing ever looked
again. The counterpart rule is that a crash must not cost work — a tree with
uncommitted changes is kept, and a tree whose ticket is still unfinished is
left for the resume path.
"""

import subprocess
from pathlib import Path

import pytest
from loregarden.models.domain import (
    AgentRun,
    RunStatus,
    Ticket,
    TicketState,
    Workspace,
    Worktree,
    WorktreeState,
)
from loregarden.services.ticket_worktree import resolve_execution_root
from loregarden.services.worktree_lifecycle import reconcile_worktrees, release_ticket_worktree
from sqlmodel import Session
from tests.worktree_helpers import git, make_repo


def _commit_in(path: Path, name: str = "work.txt") -> None:
    """Put a commit on the worktree's branch — the thing that outlives removal."""
    (path / name).write_text("real work\n")
    git(path, "add", "-A")
    git(path, "commit", "-q", "-m", "work")


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


def _ticket(session, workspace, external_id="LG-1"):
    ticket = Ticket(
        external_id=external_id,
        workspace_id=workspace.id,
        title="Add the thing",
        branch=f"loregarden/{external_id.lower()}",
    )
    session.add(ticket)
    session.commit()
    session.refresh(ticket)
    return ticket


def _tree_for(session, workspace, ticket):
    run = AgentRun(
        run_code=f"run-{ticket.external_id}",
        ticket_id=ticket.id,
        workspace_id=workspace.id,
        agent_id="backend_implementer",
        status=RunStatus.RUNNING,
    )
    session.add(run)
    session.commit()
    session.refresh(run)
    return resolve_execution_root(session, run, ticket, workspace)


def _row(session, ticket):
    return session.exec(
        Worktree.__table__.select().where(Worktree.__table__.c.ticket_id == ticket.id)
    ).first()


def test_finishing_a_ticket_removes_its_worktree(session, workspace):
    """The intended path: the work is committed to the branch, so only the
    checkout is thrown away and the commits stay in the object store."""
    ticket = _ticket(session, workspace)
    path = _tree_for(session, workspace, ticket)
    _commit_in(path)
    ticket.state = TicketState.DONE
    session.add(ticket)
    session.commit()

    assert release_ticket_worktree(session, ticket) is True
    assert not path.exists()
    assert _row(session, ticket).state == WorktreeState.CLEANUP.value


def test_an_unfinished_ticket_keeps_its_worktree(session, workspace):
    ticket = _ticket(session, workspace)
    path = _tree_for(session, workspace, ticket)

    assert release_ticket_worktree(session, ticket) is False
    assert path.is_dir()


def test_uncommitted_work_is_never_deleted(session, workspace):
    """Marking a ticket done by hand while an agent's edits sit unstaged must
    not throw those edits away."""
    ticket = _ticket(session, workspace)
    path = _tree_for(session, workspace, ticket)
    (path / "unsaved.txt").write_text("not committed\n")
    ticket.state = TicketState.WONT_DO
    session.add(ticket)
    session.commit()

    assert release_ticket_worktree(session, ticket) is False
    assert (path / "unsaved.txt").exists()


def test_startup_settles_a_row_whose_directory_is_gone(session, workspace):
    ticket = _ticket(session, workspace)
    path = _tree_for(session, workspace, ticket)
    subprocess.run(
        ["git", "worktree", "remove", "--force", str(path)],
        cwd=session.get(Workspace, workspace.id).repo_path,
        check=True,
        capture_output=True,
    )

    assert reconcile_worktrees(session) == 1
    assert _row(session, ticket).state == WorktreeState.CLEANUP.value


def test_startup_removes_the_tree_of_a_ticket_that_finished(session, workspace):
    ticket = _ticket(session, workspace)
    path = _tree_for(session, workspace, ticket)
    _commit_in(path)
    ticket.state = TicketState.DONE
    session.add(ticket)
    session.commit()

    assert reconcile_worktrees(session) == 1
    assert not path.exists()


def test_a_tracked_modification_is_never_deleted(session, workspace):
    """The untracked case has a sibling: an agent editing a file that already
    existed leaves nothing new on disk to notice, only a changed one."""
    ticket = _ticket(session, workspace)
    path = _tree_for(session, workspace, ticket)
    _commit_in(path)
    (path / "seed.txt").write_text("edited, never committed\n")
    ticket.state = TicketState.DONE
    session.add(ticket)
    session.commit()

    assert release_ticket_worktree(session, ticket) is False
    assert (path / "seed.txt").read_text() == "edited, never committed\n"


def test_untracked_work_survives_a_repo_that_hides_untracked_files(session, workspace):
    """`status.showUntrackedFiles=no` turns a bare `--porcelain` into a rubber
    stamp: git reports a clean tree while the agent's new files sit in it."""
    ticket = _ticket(session, workspace)
    path = _tree_for(session, workspace, ticket)
    _commit_in(path)
    git(path, "config", "status.showUntrackedFiles", "no")
    (path / "brand-new.py").write_text("never committed\n")
    ticket.state = TicketState.DONE
    session.add(ticket)
    session.commit()

    assert release_ticket_worktree(session, ticket) is False
    assert (path / "brand-new.py").exists()


def test_a_branch_with_nothing_committed_is_never_retired(session, workspace):
    """The external-harness shape: the tree is clean because nothing was ever
    written back, and the branch carries no commits, so the object store would
    hold nothing after the checkout went. Refuse rather than assume."""
    ticket = _ticket(session, workspace)
    path = _tree_for(session, workspace, ticket)
    ticket.state = TicketState.DONE
    session.add(ticket)
    session.commit()

    assert release_ticket_worktree(session, ticket) is False
    assert path.is_dir()
    assert _row(session, ticket).state == WorktreeState.ACTIVE.value


def test_startup_keeps_a_finished_tickets_tree_when_nothing_was_committed(session, workspace):
    """Reconciliation retires on the same signal and needs the same proof."""
    ticket = _ticket(session, workspace)
    path = _tree_for(session, workspace, ticket)
    ticket.state = TicketState.DONE
    session.add(ticket)
    session.commit()

    assert reconcile_worktrees(session) == 0
    assert path.is_dir()
    assert _row(session, ticket).state == WorktreeState.ACTIVE.value


def test_startup_leaves_a_live_ticket_alone_so_the_resume_finds_its_work(session, workspace):
    """The crash-and-restart case: the run died, the tree survived, and the
    resume path needs it exactly as it was."""
    ticket = _ticket(session, workspace)
    path = _tree_for(session, workspace, ticket)
    (path / "half-done.txt").write_text("survived the crash\n")

    assert reconcile_worktrees(session) == 0
    assert (Path(path) / "half-done.txt").exists()
    assert _row(session, ticket).state == WorktreeState.ACTIVE.value
