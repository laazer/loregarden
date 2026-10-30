"""A CLEANUP worktrees row whose checkout is still on disk.

lg-external-harness-954's re-entry half. A failed removal used to read as a
removal, leaving a directory owned by nothing; the writers now stamp CLEANUP
only once the checkout is gone, and this is the invariant that catches the
state anyway.
"""

from __future__ import annotations

from datetime import datetime
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
from loregarden.services.cleaned_worktree_rows import cleaned_rows_with_their_checkout
from loregarden.services.invariants import run_invariant
from sqlmodel import Session
from tests.worktree_helpers import make_repo

INVARIANT = invariant_registry.CLEANED_WORKTREE_ROWS_LEAVE_NO_CHECKOUT


@pytest.fixture(name="session")
def session_fixture(client, isolated_db):
    """Seeded (via `client`): a reported finding files a ticket, which needs a template."""
    with Session(isolated_db) as session:
        yield session


@pytest.fixture(name="workspace")
def workspace_fixture(session, tmp_path):
    ws = Workspace(slug="proj", name="proj", repo_path=str(make_repo(tmp_path)))
    session.add(ws)
    session.commit()
    session.refresh(ws)
    return ws


def _row(session, workspace, *, path: str, external_id: str, state: WorktreeState) -> Worktree:
    ticket = Ticket(
        external_id=external_id,
        workspace_id=workspace.id,
        title="Add the thing",
        branch=f"loregarden/{external_id}",
        state=TicketState.DONE,
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
        worktree_path=path,
        branch=ticket.branch,
        state=state,
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


def _checkout(tmp_path: Path, name: str) -> str:
    path = tmp_path / "checkouts" / name
    path.mkdir(parents=True)
    return str(path)


def test_a_cleanup_row_whose_checkout_survived_is_found(session, workspace, tmp_path):
    row = _row(
        session,
        workspace,
        path=_checkout(tmp_path, "left-behind"),
        external_id="LG-LEFT",
        state=WorktreeState.CLEANUP,
    )

    assert [r.id for r in cleaned_rows_with_their_checkout(session)] == [row.id]


def test_a_cleanup_row_whose_checkout_is_gone_is_not(session, workspace, tmp_path):
    _row(
        session,
        workspace,
        path=str(tmp_path / "checkouts" / "removed"),
        external_id="LG-GONE",
        state=WorktreeState.CLEANUP,
    )

    assert cleaned_rows_with_their_checkout(session) == []


def test_a_path_a_later_active_row_owns_is_not_a_violation(session, workspace, tmp_path):
    """The same spelling cut again: the directory is the new row's, and the old
    row's CLEANUP was true when it was written."""
    path = _checkout(tmp_path, "reused")
    _row(session, workspace, path=path, external_id="LG-OLD", state=WorktreeState.CLEANUP)
    _row(session, workspace, path=path, external_id="LG-NEW", state=WorktreeState.ACTIVE)

    assert cleaned_rows_with_their_checkout(session) == []


def test_a_file_where_the_checkout_was_is_not_a_checkout(session, workspace, tmp_path):
    path = tmp_path / "checkouts" / "a-file"
    path.parent.mkdir(parents=True)
    path.write_text("not a directory\n")
    _row(session, workspace, path=str(path), external_id="LG-FILE", state=WorktreeState.CLEANUP)

    assert cleaned_rows_with_their_checkout(session) == []


def test_the_invariant_reports_backlog_and_repairs_nothing(session, workspace, tmp_path):
    """Cleaned before the guard landed: backlog, reported on every pass and
    never escalated, and the directory is left for a person to judge."""
    path = _checkout(tmp_path, "left-behind")
    row = _row(session, workspace, path=path, external_id="LG-REPORT", state=WorktreeState.CLEANUP)
    row.cleaned_at = datetime(2026, 10, 1)
    session.add(row)
    session.commit()

    check = run_invariant(session, INVARIANT)

    assert check.found == 1
    assert check.reentered == 0
    assert check.unrepaired == 1
    assert Path(path).is_dir(), "report-only: the directory is a person's to judge"
    session.refresh(row)
    assert row.state == WorktreeState.CLEANUP


def test_it_is_registered_as_a_report_only_defect_and_scheduled():
    assert INVARIANT in invariant_registry.INVARIANTS
    assert INVARIANT.reentry is InvariantReentry.DEFECT
    assert INVARIANT.report_only
    assert INVARIANT.name in {step.name for step in reconciliation.PERIODIC_STEPS}
