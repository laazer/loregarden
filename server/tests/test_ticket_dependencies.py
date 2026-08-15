"""Ticket dependency edges and dependency-aware child ordering. Integration-review
behaviour (review runs last; backfill; Studio commit) lives in
test_auto_mode_subtree.py and test_studio_workflow_assignment.py.
"""

import pytest
from loregarden.models.domain import Ticket, WorkItemType, Workspace
from loregarden.services.subtree_auto_run import order_children_for_subtree
from loregarden.services.ticket_dependencies import (
    DependencyCycleError,
    TicketDependencyService,
)
from sqlmodel import Session, select
from tests.factories import make_ticket


def _ticket(
    tid: str,
    *,
    wtype: WorkItemType = WorkItemType.CAPABILITY,
    review: bool = False,
    priority: int = 3,
) -> Ticket:
    return Ticket(
        id=tid,
        external_id=tid,
        workspace_id="ws",
        title=tid,
        work_item_type=wtype,
        is_integration_review=review,
        priority=priority,
    )


# --- order_children_for_subtree (pure) -------------------------------------


def _rows(session: Session, *ticket_ids: str) -> None:
    """`ticket_dependencies` names a ticket at both ends of every edge."""
    workspace = session.exec(select(Workspace).where(Workspace.slug == "loregarden")).one()
    for ticket_id in ticket_ids:
        make_ticket(session, workspace_id=workspace.id, ticket_id=ticket_id)


def test_order_puts_prerequisite_before_dependent():
    a, b, review = _ticket("a"), _ticket("b"), _ticket("zzz-review", review=True)
    # review sorts FIRST by child_sort_key tie-break (nothing here beats it on type),
    # but depends on both, so it must still land last.
    ordered = [t.id for t in order_children_for_subtree([review, a, b], {"zzz-review": {"a", "b"}})]
    assert ordered[-1] == "zzz-review"
    assert set(ordered[:2]) == {"a", "b"}


def test_order_follows_a_dependency_chain():
    a, b, c = _ticket("a"), _ticket("b"), _ticket("c")
    ordered = [t.id for t in order_children_for_subtree([a, b, c], {"a": {"b"}, "b": {"c"}})]
    assert ordered == ["c", "b", "a"]


def test_order_tiebreaks_by_sort_key_without_deps():
    feature = _ticket("f", wtype=WorkItemType.FEATURE)
    cap = _ticket("cap", wtype=WorkItemType.CAPABILITY)
    task = _ticket("t", wtype=WorkItemType.TASK)
    ordered = [t.id for t in order_children_for_subtree([task, cap, feature], {})]
    assert ordered == ["f", "cap", "t"]


def test_order_ignores_prereqs_outside_the_sibling_set():
    a = _ticket("a")
    # prereq "external" is not among the children — must not stall ordering.
    ordered = [t.id for t in order_children_for_subtree([a], {"a": {"external"}})]
    assert ordered == ["a"]


def test_order_degrades_gracefully_on_a_cycle():
    a, b = _ticket("a"), _ticket("b")
    ordered = [t.id for t in order_children_for_subtree([a, b], {"a": {"b"}, "b": {"a"}})]
    assert set(ordered) == {"a", "b"} and len(ordered) == 2


# --- TicketDependencyService -----------------------------------------------


def test_add_dependency_is_idempotent_and_rejects_self(db_session: Session):
    _rows(db_session, "t1", "t2")
    svc = TicketDependencyService(db_session)
    first = svc.add_dependency("t1", "t2")
    again = svc.add_dependency("t1", "t2")
    assert first.id == again.id
    with pytest.raises(ValueError):
        svc.add_dependency("t1", "t1")


def test_add_dependency_rejects_a_cycle(db_session: Session):
    _rows(db_session, "a", "b", "c")
    svc = TicketDependencyService(db_session)
    svc.add_dependency("a", "b")  # a waits for b
    svc.add_dependency("b", "c")  # b waits for c
    with pytest.raises(DependencyCycleError):
        svc.add_dependency("c", "a")  # c waits for a would close a->b->c->a


def test_prerequisites_map_scopes_to_requested_ids(db_session: Session):
    _rows(db_session, "a", "b", "c")
    svc = TicketDependencyService(db_session)
    svc.add_dependency("a", "b")
    svc.add_dependency("a", "c")
    mapping = svc.prerequisites_map(["a", "b"])
    assert mapping["a"] == {"b", "c"}
    assert mapping["b"] == set()


def test_remove_dependency(db_session: Session):
    _rows(db_session, "a", "b")
    svc = TicketDependencyService(db_session)
    svc.add_dependency("a", "b")
    assert svc.remove_dependency("a", "b") is True
    assert svc.prerequisites("a") == []
    assert svc.remove_dependency("a", "b") is False


# --- review_child_type -----------------------------------------------------


def test_review_child_type_by_parent():
    from loregarden.services.integration_review import review_child_type

    assert review_child_type(WorkItemType.FEATURE) == WorkItemType.CAPABILITY
    assert review_child_type(WorkItemType.MILESTONE) == WorkItemType.FEATURE
    assert review_child_type(WorkItemType.CAPABILITY) is None
    assert review_child_type(WorkItemType.TASK) is None
    assert review_child_type(None) is None
