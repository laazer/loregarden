"""The autopilot rooted at a milestone or parent ticket, not only an initiative.

Same engine as the initiative's (`initiative_autopilot`); these pin what changes
when the root is a parent: the plan is its subtree as one phase, the parent
itself is never started, a run of the parent (or above it) holds its children
back, and nested plans each keep their own cap.
"""

from __future__ import annotations

import pytest
from loregarden.mcp.tools import execute_tool
from loregarden.models.domain import (
    AutopilotAction,
    AutopilotEvent,
    AutopilotUpdate,
    InitiativePlan,
    OrchestrationRun,
    OrchestrationRunStatus,
    Ticket,
    WorkItemType,
)
from loregarden.services.initiative_autopilot import (
    AutopilotRefusedError,
    run_autopilot,
    set_autopilot,
)
from loregarden.services.subtree_autopilot import subtree_autopilot_view
from loregarden.services.ticket_service import TicketService
from sqlmodel import Session, select
from tests.test_initiative_graph import (  # noqa: F401 - fixtures are used by name
    NOW,
    _initiative,
    _item,
    _milestone,
    _queued,
    queue_fixture,
    workspaces_fixture,
)


def _standalone_milestone(session: Session, workspace) -> Ticket:
    return TicketService(session).create_ticket(
        workspace_slug=workspace.slug, title="Solo", work_item_type=WorkItemType.MILESTONE
    )


def _running(session: Session, ticket: Ticket) -> None:
    session.add(
        OrchestrationRun(
            run_code=f"orch_{ticket.external_id}",
            ticket_id=ticket.id,
            workspace_id=ticket.workspace_id,
            status=OrchestrationRunStatus.RUNNING,
        )
    )
    session.commit()


def _on(session: Session, root: Ticket, max_parallel: int | None = None) -> None:
    set_autopilot(
        session, root.id, AutopilotUpdate(enabled=True, max_parallel=max_parallel), actor="t"
    )


def test_a_milestone_outside_any_initiative_runs_its_items_up_to_its_cap(
    db_session, workspaces, queue
):
    lanes, _ = queue
    here, _ = workspaces
    m = _standalone_milestone(db_session, here)
    items = [_item(db_session, here, m, f"f{i}") for i in range(4)]
    _on(db_session, m, max_parallel=2)

    assert run_autopilot(db_session, m.id, now=NOW) == 2

    queued = _queued(lanes)
    assert len(queued) == 2
    assert set(queued) <= {t.id for t in items}
    assert m.id not in queued


def test_a_feature_plans_only_its_own_subtree(db_session, workspaces, queue):
    lanes, _ = queue
    here, _ = workspaces
    m = _standalone_milestone(db_session, here)
    feature = _item(db_session, here, m, "feature")
    sibling = _item(db_session, here, m, "sibling")
    cap = _item(db_session, here, feature, "cap")
    cap.work_item_type = WorkItemType.CAPABILITY
    db_session.add(cap)
    db_session.commit()
    _on(db_session, feature)

    run_autopilot(db_session, feature.id, now=NOW)

    assert _queued(lanes) == [cap.id]
    assert sibling.id not in _queued(lanes)


def test_a_childless_parent_cannot_be_turned_on(db_session, workspaces):
    here, _ = workspaces
    m = _standalone_milestone(db_session, here)
    with pytest.raises(AutopilotRefusedError, match="no sub-items"):
        _on(db_session, m)
    assert db_session.get(InitiativePlan, m.id) is None


def test_a_leaf_ticket_is_not_a_plan_root(db_session, workspaces):
    here, _ = workspaces
    m = _standalone_milestone(db_session, here)
    task = _item(db_session, here, m, "task")
    task.work_item_type = WorkItemType.TASK
    db_session.add(task)
    db_session.commit()
    with pytest.raises(LookupError):
        _on(db_session, task)


def test_a_parent_left_childless_is_never_started_itself(db_session, workspaces, queue):
    lanes, _ = queue
    here, _ = workspaces
    m = _standalone_milestone(db_session, here)
    child = _item(db_session, here, m, "only")
    _on(db_session, m)
    child.parent_ticket_id = None
    db_session.add(child)
    db_session.commit()

    assert run_autopilot(db_session, m.id, now=NOW) == 0
    assert _queued(lanes) == []
    assert subtree_autopilot_view(db_session, m.id).autopilot.next_up == []


def test_items_wait_while_their_parent_runs_and_say_so_once(db_session, workspaces, queue):
    lanes, _ = queue
    here, _ = workspaces
    m = _standalone_milestone(db_session, here)
    item = _item(db_session, here, m, "f")
    _running(db_session, m)
    _on(db_session, m)

    run_autopilot(db_session, m.id, now=NOW)
    run_autopilot(db_session, m.id, now=NOW)

    assert _queued(lanes) == []
    refusals = db_session.exec(
        select(AutopilotEvent).where(
            AutopilotEvent.ticket_id == item.id, AutopilotEvent.action == AutopilotAction.REFUSED
        )
    ).all()
    assert len(refusals) == 1
    assert m.external_id in refusals[0].detail
    view = subtree_autopilot_view(db_session, m.id)
    assert view.running_above is not None and view.running_above.id == m.id


def test_an_initiative_waits_for_a_milestone_run_too(db_session, workspaces, queue):
    """The same guard covers an initiative whose milestone was started by hand."""
    lanes, _ = queue
    here, _ = workspaces
    initiative = _initiative(db_session)
    m = _milestone(db_session, here, "M", initiative)
    _item(db_session, here, m, "f")
    _running(db_session, m)
    _on(db_session, initiative)

    run_autopilot(db_session, initiative.id, now=NOW)

    assert _queued(lanes) == []


def test_nested_plans_each_keep_their_cap_and_the_inner_names_the_outer(
    db_session, workspaces, queue
):
    lanes, _ = queue
    here, _ = workspaces
    initiative = _initiative(db_session)
    m = _milestone(db_session, here, "M", initiative)
    for i in range(4):
        _item(db_session, here, m, f"f{i}")
    _on(db_session, initiative, max_parallel=1)
    _on(db_session, m, max_parallel=2)

    view = subtree_autopilot_view(db_session, m.id)
    assert [r.id for r in view.also_driven_by] == [initiative.id]
    assert view.total_items == 4 and view.open_items == 4
    assert len(view.next_up) == 2


def test_the_mcp_tool_turns_on_a_milestone_by_external_id(db_session, workspaces, queue):
    here, _ = workspaces
    m = _standalone_milestone(db_session, here)
    _item(db_session, here, m, "f")

    execute_tool(
        db_session,
        "loregarden_set_initiative_autopilot",
        {"initiative_id": m.external_id, "enabled": True},
    )

    plan = db_session.get(InitiativePlan, m.id)
    assert plan is not None and plan.autopilot


# --- API ---------------------------------------------------------------------


def test_ticket_autopilot_endpoints(client, db_session, workspaces, queue):
    here, _ = workspaces
    m = _standalone_milestone(db_session, here)
    empty = _standalone_milestone(db_session, here)
    _item(db_session, here, m, "f")

    got = client.get(f"/api/tickets/{m.id}/autopilot")
    assert got.status_code == 200, got.text
    assert got.json()["autopilot"]["enabled"] is False

    on = client.patch(f"/api/tickets/{m.id}/autopilot", json={"enabled": True, "max_parallel": 4})
    assert on.status_code == 200, on.text
    assert on.json()["autopilot"]["enabled"] is True
    assert on.json()["autopilot"]["max_parallel"] == 4

    assert (
        client.patch(f"/api/tickets/{empty.id}/autopilot", json={"enabled": True}).status_code
        == 409
    )
    assert client.get("/api/tickets/nope/autopilot").status_code == 404


def test_the_initiative_endpoint_still_takes_only_initiatives(client, db_session, workspaces):
    here, _ = workspaces
    m = _standalone_milestone(db_session, here)
    _item(db_session, here, m, "f")

    res = client.patch(f"/api/initiatives/{m.id}/autopilot", json={"enabled": True})

    assert res.status_code == 404
    assert db_session.get(InitiativePlan, m.id) is None
