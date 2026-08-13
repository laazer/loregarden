"""A parent's state follows its children.

Nothing moved a parent except orchestrating the parent itself, and the queue now
runs one ticket per lane — so children routinely finish without their parent ever
running, and a feature whose every child is done stayed "in progress" forever.
"""

import pytest
from loregarden.models.domain import Ticket, TicketState, WorkItemType, Workspace
from loregarden.services.ticket_rollup import (
    derive_parent_state,
    reconcile_all_parents,
    reconcile_ancestors,
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
    session.refresh(ws)
    return ws


def _ticket(
    session: Session,
    workspace,
    code: str,
    *,
    state: TicketState = TicketState.BACKLOG,
    parent: Ticket | None = None,
    work_item_type: WorkItemType = WorkItemType.TASK,
    locked: bool = False,
) -> Ticket:
    ticket = Ticket(
        external_id=code,
        workspace_id=workspace.id,
        title=code,
        state=state,
        work_item_type=work_item_type,
        parent_ticket_id=parent.id if parent else None,
        state_locked=locked,
    )
    session.add(ticket)
    session.commit()
    session.refresh(ticket)
    return ticket


# ---- the rule itself ---------------------------------------------------


@pytest.mark.parametrize(
    ("children", "expected"),
    [
        ([], None),
        ([TicketState.DONE, TicketState.DONE], TicketState.DONE),
        # wont_do is a resolution, not a gap.
        ([TicketState.DONE, TicketState.WONT_DO], TicketState.DONE),
        ([TicketState.BACKLOG, TicketState.BACKLOG], TicketState.BACKLOG),
        ([TicketState.DONE, TicketState.BACKLOG], TicketState.IN_PROGRESS),
        ([TicketState.IN_PROGRESS], TicketState.IN_PROGRESS),
        # Blocked outranks "mostly finished" — it is the urgent fact about a tree.
        ([TicketState.DONE, TicketState.BLOCKED], TicketState.BLOCKED),
        ([TicketState.BLOCKED, TicketState.BACKLOG], TicketState.BLOCKED),
    ],
)
def test_derive_parent_state(children, expected):
    assert derive_parent_state(children) is expected


# ---- pushing upward ----------------------------------------------------


def test_finishing_the_last_child_finishes_the_parent(session, workspace):
    parent = _ticket(
        session,
        workspace,
        "F-1",
        state=TicketState.IN_PROGRESS,
        work_item_type=WorkItemType.FEATURE,
    )
    _ticket(session, workspace, "T-1", state=TicketState.DONE, parent=parent)
    last = _ticket(session, workspace, "T-2", state=TicketState.DONE, parent=parent)

    changed = reconcile_ancestors(session, last)

    assert [t.id for t in changed] == [parent.id]
    session.refresh(parent)
    assert parent.state == TicketState.DONE
    assert parent.last_updated_by == "rollup"


def test_rollup_climbs_the_whole_chain(session, workspace):
    """A task finishing can complete its capability, feature and milestone."""
    milestone = _ticket(
        session,
        workspace,
        "M-1",
        state=TicketState.IN_PROGRESS,
        work_item_type=WorkItemType.MILESTONE,
    )
    feature = _ticket(
        session,
        workspace,
        "F-1",
        state=TicketState.IN_PROGRESS,
        work_item_type=WorkItemType.FEATURE,
        parent=milestone,
    )
    capability = _ticket(
        session,
        workspace,
        "C-1",
        state=TicketState.IN_PROGRESS,
        work_item_type=WorkItemType.CAPABILITY,
        parent=feature,
    )
    task = _ticket(session, workspace, "T-1", state=TicketState.DONE, parent=capability)

    changed = reconcile_ancestors(session, task)

    assert {t.external_id for t in changed} == {"C-1", "F-1", "M-1"}
    for node in (capability, feature, milestone):
        session.refresh(node)
        assert node.state == TicketState.DONE


def test_rollup_stops_at_the_first_ancestor_that_does_not_move(session, workspace):
    milestone = _ticket(
        session,
        workspace,
        "M-1",
        state=TicketState.IN_PROGRESS,
        work_item_type=WorkItemType.MILESTONE,
    )
    _ticket(session, workspace, "F-other", state=TicketState.BACKLOG, parent=milestone)
    feature = _ticket(
        session,
        workspace,
        "F-1",
        state=TicketState.IN_PROGRESS,
        work_item_type=WorkItemType.FEATURE,
        parent=milestone,
    )
    task = _ticket(session, workspace, "T-1", state=TicketState.DONE, parent=feature)

    changed = reconcile_ancestors(session, task)

    # The feature completes; the milestone still has an open sibling feature.
    assert [t.external_id for t in changed] == ["F-1"]
    session.refresh(milestone)
    assert milestone.state == TicketState.IN_PROGRESS


def test_a_blocked_child_blocks_the_parent(session, workspace):
    parent = _ticket(
        session,
        workspace,
        "F-1",
        state=TicketState.IN_PROGRESS,
        work_item_type=WorkItemType.FEATURE,
    )
    _ticket(session, workspace, "T-1", state=TicketState.DONE, parent=parent)
    stuck = _ticket(session, workspace, "T-2", state=TicketState.BLOCKED, parent=parent)

    reconcile_ancestors(session, stuck)

    session.refresh(parent)
    assert parent.state == TicketState.BLOCKED


def test_reopening_a_child_reopens_the_parent(session, workspace):
    parent = _ticket(
        session, workspace, "F-1", state=TicketState.DONE, work_item_type=WorkItemType.FEATURE
    )
    child = _ticket(session, workspace, "T-1", state=TicketState.IN_PROGRESS, parent=parent)

    reconcile_ancestors(session, child)

    session.refresh(parent)
    assert parent.state == TicketState.IN_PROGRESS


# ---- what it must not touch --------------------------------------------


def test_a_locked_parent_is_left_alone(session, workspace):
    """`state_locked` is how an operator says "I decided this"."""
    parent = _ticket(
        session,
        workspace,
        "F-1",
        state=TicketState.IN_PROGRESS,
        work_item_type=WorkItemType.FEATURE,
        locked=True,
    )
    child = _ticket(session, workspace, "T-1", state=TicketState.DONE, parent=parent)

    assert reconcile_ancestors(session, child) == []
    session.refresh(parent)
    assert parent.state == TicketState.IN_PROGRESS


def test_an_abandoned_parent_is_left_alone(session, workspace):
    parent = _ticket(
        session, workspace, "F-1", state=TicketState.WONT_DO, work_item_type=WorkItemType.FEATURE
    )
    child = _ticket(session, workspace, "T-1", state=TicketState.DONE, parent=parent)

    assert reconcile_ancestors(session, child) == []
    session.refresh(parent)
    assert parent.state == TicketState.WONT_DO


def test_a_childless_ticket_has_no_parent_to_move(session, workspace):
    orphan = _ticket(session, workspace, "T-1", state=TicketState.DONE)
    assert reconcile_ancestors(session, orphan) == []


def test_a_parent_cycle_does_not_hang(session, workspace):
    """The schema permits what the hierarchy rules forbid."""
    a = _ticket(session, workspace, "A", state=TicketState.IN_PROGRESS)
    b = _ticket(session, workspace, "B", state=TicketState.IN_PROGRESS, parent=a)
    a.parent_ticket_id = b.id
    session.add(a)
    session.commit()

    reconcile_ancestors(session, b)  # must return rather than loop


# ---- the startup sweep -------------------------------------------------


def test_the_sweep_corrects_stale_parents(session, workspace):
    stale = _ticket(
        session,
        workspace,
        "F-1",
        state=TicketState.IN_PROGRESS,
        work_item_type=WorkItemType.FEATURE,
    )
    _ticket(session, workspace, "T-1", state=TicketState.DONE, parent=stale)
    _ticket(session, workspace, "T-2", state=TicketState.DONE, parent=stale)

    changed = reconcile_all_parents(session)

    assert [t.external_id for t in changed] == ["F-1"]
    session.refresh(stale)
    assert stale.state == TicketState.DONE


def test_the_sweep_settles_a_whole_tree_in_one_pass(session, workspace):
    """Deepest first, or a corrected capability would not reach its milestone
    until the next boot."""
    milestone = _ticket(
        session,
        workspace,
        "M-1",
        state=TicketState.IN_PROGRESS,
        work_item_type=WorkItemType.MILESTONE,
    )
    feature = _ticket(
        session,
        workspace,
        "F-1",
        state=TicketState.IN_PROGRESS,
        work_item_type=WorkItemType.FEATURE,
        parent=milestone,
    )
    capability = _ticket(
        session,
        workspace,
        "C-1",
        state=TicketState.IN_PROGRESS,
        work_item_type=WorkItemType.CAPABILITY,
        parent=feature,
    )
    _ticket(session, workspace, "T-1", state=TicketState.DONE, parent=capability)

    reconcile_all_parents(session)

    for node in (capability, feature, milestone):
        session.refresh(node)
        assert node.state == TicketState.DONE, f"{node.external_id} did not settle in one pass"


def test_the_sweep_is_idempotent(session, workspace):
    parent = _ticket(
        session,
        workspace,
        "F-1",
        state=TicketState.IN_PROGRESS,
        work_item_type=WorkItemType.FEATURE,
    )
    _ticket(session, workspace, "T-1", state=TicketState.DONE, parent=parent)

    assert len(reconcile_all_parents(session)) == 1
    assert reconcile_all_parents(session) == []
