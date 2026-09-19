"""Ticket dependency edges and dependency-aware child ordering. Integration-review
behaviour (review runs last; backfill; Studio commit) lives in
test_auto_mode_subtree.py and test_studio_workflow_assignment.py.
"""

import pytest
from loregarden.mcp.ticket_edit_tools import ticket_state_payload
from loregarden.models.domain import Ticket, TicketState, WorkItemType, Workspace
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


# --- unmet_prerequisites: may this ticket start at all? ---------------------
#
# `order_children_for_subtree` above answers a different question — what order
# should these siblings run in — and drops every edge pointing outside the
# sibling set. That is every cross-workspace edge. Nine of those already existed
# when this was written, rendered in the UI as prerequisites and affecting
# nothing (676).


def _other_workspace(db_session: Session) -> Workspace:
    workspace = Workspace(id="ws-lore-eden", slug="lore-eden", name="lore-eden")
    db_session.add(workspace)
    db_session.commit()
    return workspace


def test_an_unfinished_prerequisite_is_unmet(db_session: Session) -> None:
    _rows(db_session, "waiter", "prereq")
    TicketDependencyService(db_session).add_dependency("waiter", "prereq")

    unmet = TicketDependencyService(db_session).unmet_prerequisites("waiter")

    assert [t.id for t in unmet] == ["prereq"]


@pytest.mark.parametrize("state", [TicketState.DONE, TicketState.WONT_DO])
def test_a_settled_prerequisite_stops_blocking(db_session: Session, state: TicketState) -> None:
    """`wont_do` counts as settled, and that is deliberate.

    Work that will never be done cannot be waited for. Treating it as unmet is
    how a dependency graph deadlocks on a decision somebody already took.
    """
    _rows(db_session, "waiter", "prereq")
    TicketDependencyService(db_session).add_dependency("waiter", "prereq")
    db_session.get(Ticket, "prereq").state = state
    db_session.commit()

    assert TicketDependencyService(db_session).unmet_prerequisites("waiter") == []


def test_a_prerequisite_in_another_workspace_is_unmet_like_any_other(db_session: Session) -> None:
    """The whole point: an edge is two ticket ids, and nothing in this path
    filters by workspace. Cross-workspace support is the absence of a
    restriction, not a feature bolted on beside one."""
    _rows(db_session, "waiter")
    other = _other_workspace(db_session)
    make_ticket(db_session, workspace_id=other.id, ticket_id="lor-extract-lore-35")
    TicketDependencyService(db_session).add_dependency("waiter", "lor-extract-lore-35")

    unmet = TicketDependencyService(db_session).unmet_prerequisites("waiter")

    assert [t.id for t in unmet] == ["lor-extract-lore-35"]
    assert unmet[0].workspace_id == other.id


def test_ordering_still_ignores_the_edge_that_blocking_honours(db_session: Session) -> None:
    """The two consumers must stay different, and this pins the seam.

    A cross-set prerequisite may not reorder siblings — there is nothing in the
    set to run first — but it must still stop the dependent from starting. Fold
    them together and either sibling ordering starts hanging on tickets it
    cannot see, or blocking goes back to ignoring the edges that matter most.
    """
    _rows(db_session, "waiter")
    other = _other_workspace(db_session)
    make_ticket(db_session, workspace_id=other.id, ticket_id="outsider")
    TicketDependencyService(db_session).add_dependency("waiter", "outsider")
    siblings = [_ticket("waiter")]

    prereqs = TicketDependencyService(db_session).prerequisites_map(["waiter"])
    ordered = order_children_for_subtree(siblings, prereqs)

    assert [t.id for t in ordered] == ["waiter"], "ordering must ignore the outside edge"
    assert TicketDependencyService(db_session).unmet_prerequisites("waiter"), "blocking must not"


# --- the MCP surface: what a reader is told about the far end ---------------


def test_an_edge_names_the_workspace_it_points_into(db_session: Session) -> None:
    """676: `depends_on: lor-extract-lore-35` is not actionable on its own.

    The far end of an edge need not be in this workspace — nine such edges
    already existed — and the board the reader is looking at does not show the
    other one. Without the workspace the id sends them to the wrong place, which
    is worse than saying nothing.
    """
    _rows(db_session, "waiter")
    other = _other_workspace(db_session)
    make_ticket(db_session, workspace_id=other.id, ticket_id="lor-extract-lore-35")
    TicketDependencyService(db_session).add_dependency("waiter", "lor-extract-lore-35")

    payload = ticket_state_payload(db_session, "waiter")

    assert [e["workspace"] for e in payload["depends_on"]] == ["lore-eden"]


def test_blocked_by_is_the_subset_actually_holding_the_ticket_up(db_session: Session) -> None:
    """`depends_on` lists edges; `blocked_by` answers the question.

    A reader with only `depends_on` has to fetch each far end and check its
    state — and for a cross-workspace edge they may not be able to. The two
    differ here precisely because one prerequisite is settled and the other is
    not, which is the only case where the distinction earns its place.
    """
    _rows(db_session, "waiter", "finished")
    other = _other_workspace(db_session)
    make_ticket(db_session, workspace_id=other.id, ticket_id="still-open")
    db_session.get(Ticket, "finished").state = TicketState.DONE
    db_session.commit()
    svc = TicketDependencyService(db_session)
    svc.add_dependency("waiter", "finished")
    svc.add_dependency("waiter", "still-open")

    payload = ticket_state_payload(db_session, "waiter")

    assert {e["external_id"] for e in payload["depends_on"]} == {"finished", "still-open"}
    assert [e["external_id"] for e in payload["blocked_by"]] == ["still-open"]
