"""A checkout on disk whose recorded branch is gone.

lg-external-harness-1166's re-entry half. A losing fan-out attempt whose
removal failed had its branch force-deleted anyway, stranding the checkout's
commits; the discard now keeps the branch, and this is the invariant that
catches the state anyway.
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest
from loregarden.models.domain import (
    AgentRun,
    InvariantReentry,
    RunStatus,
    Ticket,
    TicketState,
    Workspace,
    Worktree,
    WorktreeState,
)
from loregarden.services import invariant_registry, reconciliation
from loregarden.services.checkouts_without_their_branch import checkouts_without_their_branch
from loregarden.services.invariants import run_invariant
from sqlmodel import Session
from tests.worktree_helpers import git, make_repo

INVARIANT = invariant_registry.CHECKOUTS_KEEP_THEIR_BRANCH


@pytest.fixture(name="session")
def session_fixture(client, isolated_db):
    """Seeded (via `client`): a reported finding files a ticket, which needs a template."""
    with Session(isolated_db) as session:
        yield session


@pytest.fixture(name="repo")
def repo_fixture(tmp_path) -> Path:
    return make_repo(tmp_path)


@pytest.fixture(name="workspace")
def workspace_fixture(session, repo):
    ws = Workspace(slug="proj", name="proj", repo_path=str(repo))
    session.add(ws)
    session.commit()
    session.refresh(ws)
    return ws


def _row(session, workspace, *, path: Path, branch: str, external_id: str) -> Worktree:
    ticket = Ticket(
        external_id=external_id,
        workspace_id=workspace.id,
        title="Add the thing",
        branch=branch,
        state=TicketState.IN_PROGRESS,
    )
    session.add(ticket)
    session.commit()
    session.refresh(ticket)
    run = AgentRun(
        run_code=f"run-{external_id}",
        ticket_id=ticket.id,
        workspace_id=workspace.id,
        agent_id="backend_implementer",
        status=RunStatus.SUCCEEDED,
    )
    session.add(run)
    session.commit()
    session.refresh(run)
    row = Worktree(
        workspace_id=workspace.id,
        agent_run_id=run.id,
        ticket_id=ticket.id,
        parent_branch="main",
        worktree_path=str(path),
        branch=branch,
        state=WorktreeState.ACTIVE,
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


def _checkout(tmp_path: Path, name: str) -> Path:
    path = tmp_path / "checkouts" / name
    path.mkdir(parents=True)
    return path


def test_a_checkout_whose_branch_exists_is_not_a_violation(session, workspace, repo, tmp_path):
    git(repo, "branch", "loregarden/kept")
    _row(
        session,
        workspace,
        path=_checkout(tmp_path, "kept"),
        branch="loregarden/kept",
        external_id="LG-KEPT",
    )

    assert checkouts_without_their_branch(session) == []


def test_a_checkout_whose_branch_was_deleted_is_found(session, workspace, repo, tmp_path):
    git(repo, "branch", "loregarden/gone")
    row = _row(
        session,
        workspace,
        path=_checkout(tmp_path, "gone"),
        branch="loregarden/gone",
        external_id="LG-GONE",
    )
    git(repo, "branch", "-D", "loregarden/gone")

    assert [r.id for r in checkouts_without_their_branch(session)] == [row.id]


def test_no_checkout_on_disk_is_not_this_invariants_state(session, workspace, tmp_path):
    _row(
        session,
        workspace,
        path=tmp_path / "checkouts" / "removed",
        branch="loregarden/never-made",
        external_id="LG-REMOVED",
    )

    assert checkouts_without_their_branch(session) == []


def test_a_repo_git_cannot_read_is_skipped_and_says_so(session, workspace, tmp_path, caplog):
    """ "Git would not list the branches" is not "the branch is gone"."""
    _row(
        session,
        workspace,
        path=_checkout(tmp_path, "unreadable"),
        branch="loregarden/unknown",
        external_id="LG-UNREAD",
    )
    not_a_repo = tmp_path / "not-a-repo"
    not_a_repo.mkdir()
    workspace.repo_path = str(not_a_repo)
    session.add(workspace)
    session.commit()

    with caplog.at_level(logging.WARNING):
        assert checkouts_without_their_branch(session) == []
    assert any("Could not list branches" in r.getMessage() for r in caplog.records)


def test_the_invariant_escalates_and_repairs_nothing(session, workspace, repo, tmp_path):
    """Nothing stamps when a branch went, so a violation cannot be dated before
    the guard and reads as re-entry: escalated, never repaired."""
    git(repo, "branch", "loregarden/stranded")
    path = _checkout(tmp_path, "stranded")
    _row(session, workspace, path=path, branch="loregarden/stranded", external_id="LG-STRAND")
    git(repo, "branch", "-D", "loregarden/stranded")

    check = run_invariant(session, INVARIANT)

    assert check.found == 1
    assert check.reentered == 1
    assert check.repaired == 0
    assert path.is_dir(), "report-only: the commits may still be wanted"


def test_it_is_registered_as_a_report_only_defect_and_scheduled():
    assert INVARIANT in invariant_registry.INVARIANTS
    assert INVARIANT.reentry is InvariantReentry.DEFECT
    assert INVARIANT.report_only
    assert INVARIANT.name in {step.name for step in reconciliation.PERIODIC_STEPS}
