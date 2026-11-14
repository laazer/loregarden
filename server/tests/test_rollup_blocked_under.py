"""A parent the rollup blocks says which tickets under it are blocked, and why.

The initiative and lg-milestone-that-493 read "blocked" from grandchildren,
and 1420's `blocking_issues` was empty, so nothing on the board said which
ticket was the cause. The operator asked on four dates (10-10, 10-12, 10-27,
11-02) and on 10-12 pushed back three times on tickets that did not block it.
"""

from __future__ import annotations

import pytest
from loregarden.models.domain import BlockKind, Ticket, TicketState, WorkItemType, Workspace
from loregarden.services.ticket_rollup import (
    BLOCKED_UNDER_NOTE,
    reconcile_all_parents,
    reconcile_parent,
)
from sqlmodel import Session


@pytest.fixture(name="session")
def session_fixture(isolated_db):
    with Session(isolated_db) as session:
        yield session


@pytest.fixture(name="workspace")
def workspace_fixture(session):
    ws = Workspace(slug="proj", name="proj", repo_path=".")
    session.add(ws)
    session.commit()
    return ws


def _ticket(
    session: Session,
    workspace: Workspace,
    code: str,
    *,
    parent: Ticket | None = None,
    kind: WorkItemType = WorkItemType.TASK,
    state: TicketState = TicketState.BACKLOG,
) -> Ticket:
    ticket = Ticket(
        external_id=code,
        workspace_id=workspace.id,
        title=code,
        state=state,
        work_item_type=kind,
        parent_ticket_id=parent.id if parent else None,
    )
    session.add(ticket)
    session.commit()
    return ticket


def _block(session: Session, ticket: Ticket, kind: BlockKind, words: str) -> None:
    ticket.state = TicketState.BLOCKED
    ticket.block_kind = kind
    ticket.blocking_issues = words
    session.add(ticket)
    session.commit()


@pytest.fixture(name="chain")
def chain_fixture(session, workspace) -> tuple[Ticket, Ticket, Ticket, Ticket]:
    """493's shape: a milestone over a feature over a blocked leaf, beside an idle one."""
    milestone = _ticket(session, workspace, "lg-m-1", kind=WorkItemType.MILESTONE)
    feature = _ticket(session, workspace, "lg-f-2", parent=milestone, kind=WorkItemType.FEATURE)
    leaf = _ticket(session, workspace, "lg-t-3", parent=feature)
    _ticket(session, workspace, "lg-t-4", parent=feature)
    _block(session, leaf, BlockKind.CONFLICT, "Merging integration/x conflicts in client/a.tsx")
    reconcile_all_parents(session)
    for t in (milestone, feature):
        session.refresh(t)
    return milestone, feature, leaf, _ticket


def test_every_rolled_up_parent_names_the_leaf_and_its_reason(chain):
    milestone, feature, leaf, _ = chain

    for parent in (milestone, feature):
        assert parent.state is TicketState.BLOCKED
        assert parent.block_kind is BlockKind.TICKETS
        assert parent.blocking_issues == (
            f"{BLOCKED_UNDER_NOTE}{leaf.external_id} "
            "(conflict: Merging integration/x conflicts in client/a.tsx)"
        )
    assert feature.external_id not in milestone.blocking_issues, "the cause, not the path"


def test_once_the_leaf_is_unblocked_the_note_goes(session, chain):
    milestone, feature, leaf, _ = chain
    leaf.state = TicketState.IN_PROGRESS
    leaf.block_kind = None
    leaf.blocking_issues = ""
    session.add(leaf)
    session.commit()

    reconcile_all_parents(session)

    for parent in (milestone, feature):
        session.refresh(parent)
        assert parent.state is TicketState.IN_PROGRESS
        assert parent.blocking_issues == ""


def test_words_a_parents_own_run_wrote_are_kept(session, workspace):
    parent = _ticket(session, workspace, "lg-p-1", kind=WorkItemType.FEATURE)
    child = _ticket(session, workspace, "lg-c-2", parent=parent)
    _block(session, child, BlockKind.WORK, "tests fail")
    parent.state = TicketState.BLOCKED
    parent.blocking_issues = "The gate refused the parent's own diff."
    session.add(parent)
    session.commit()

    reconcile_parent(session, parent)

    assert parent.blocking_issues == "The gate refused the parent's own diff."


def test_many_blockers_are_named_then_counted(session, workspace):
    parent = _ticket(session, workspace, "lg-p-1", kind=WorkItemType.FEATURE)
    for i in range(5):
        _block(session, _ticket(session, workspace, f"lg-c-{i}", parent=parent), BlockKind.WORK, "")

    reconcile_parent(session, parent)

    assert parent.blocking_issues.endswith("; and 2 more"), parent.blocking_issues
    assert "lg-c-0 (work)" in parent.blocking_issues


def test_a_long_reason_is_shortened(session, workspace):
    parent = _ticket(session, workspace, "lg-p-1", kind=WorkItemType.FEATURE)
    child = _ticket(session, workspace, "lg-c-1", parent=parent)
    _block(session, child, BlockKind.WORK, "word " * 100)

    reconcile_parent(session, parent)

    assert parent.blocking_issues.endswith("…)"), parent.blocking_issues
    assert len(parent.blocking_issues) < 200
