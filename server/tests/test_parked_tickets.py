"""Parking a human-gated ticket instead of stalling everything above it.

Driving blobert milestone 14 on 2026-08-15, ticket 22 needed GPU profiler
timings a headless agent cannot capture, so it reported `blocked` — correctly,
exactly as the stage-report contract prescribes. That one report took milestone
14, feature 15 and capability 21 down with it, and 30 unrelated backlog tickets
stopped moving until an operator intervened by hand.

`blocked` should keep meaning "this run stopped and someone should look", and a
parent should keep waiting for it. `parked` is the other thing an agent needs to
be able to say: a person owes this, carry on without it
(lg-workflow-integrity-449).
"""

from __future__ import annotations

from unittest import mock

import pytest
from loregarden.models.domain import Ticket, TicketState, WorkItemType, Workspace
from loregarden.services.hierarchy_service import reparent_ticket
from loregarden.services.ticket_rollup import derive_parent_state
from loregarden.services.ticket_state_service import can_choose, derive
from sqlmodel import Session
from tests.mcp_helpers import call_mcp


def _ticket(
    session: Session,
    workspace: Workspace,
    *,
    title: str,
    state: TicketState = TicketState.BACKLOG,
    parent_id: str | None = None,
    work_item_type: WorkItemType = WorkItemType.TASK,
) -> Ticket:
    ticket = Ticket(
        external_id=f"park-{title}",
        workspace_id=workspace.id,
        title=title,
        state=state,
        work_item_type=work_item_type,
        parent_ticket_id=parent_id,
    )
    session.add(ticket)
    session.commit()
    session.refresh(ticket)
    return ticket


@pytest.fixture
def workspace(db_session: Session) -> Workspace:
    ws = Workspace(slug="park", name="Park", repo_path="/nonexistent/park")
    db_session.add(ws)
    db_session.commit()
    db_session.refresh(ws)
    return ws


def test_a_parked_child_does_not_stop_its_siblings(db_session, workspace):
    """AC1 and AC6 — the milestone 14 shape.

    The loop in `_orchestrate_incomplete_children` returns on the first blocked
    child, abandoning every sibling after it. A parked child must be stepped
    over instead, so the rest of the subtree keeps dispatching.
    """
    from loregarden.services.builtin_orchestrator import _orchestrate_incomplete_children

    parent = _ticket(
        db_session, workspace, title="capability", work_item_type=WorkItemType.CAPABILITY
    )
    _ticket(
        db_session, workspace, title="gpu-timings", state=TicketState.PARKED, parent_id=parent.id
    )
    sibling = _ticket(db_session, workspace, title="sibling", parent_id=parent.id)

    # The sibling must be *reached*. Running it for real would dispatch a CLI
    # agent, so `ticket_workflow_complete` is stubbed to record each child it is
    # asked about and report it complete. With the old code the loop returned at
    # the parked child and the sibling was never asked about at all.
    from loregarden.services import builtin_orchestrator as mod

    reached: list[str] = []

    def _complete(orch, child):
        reached.append(child.title)
        return True

    with mock.patch.object(mod, "ticket_workflow_complete", _complete):
        builtin = mod.BuiltinOrchestrator(db_session)
        reason = _orchestrate_incomplete_children(
            builtin, parent, mod.OrchestrationProfile(slug="park")
        )

    assert sibling.title in reached, "the parked child stopped its sibling being reached"
    assert "gpu-timings" not in reached, "the parked child should be stepped over, not run"
    # AC3: the parent still must not report itself complete.
    assert reason is not None
    assert "gpu-timings" in reason
    assert "parked" in reason.lower()


def test_a_blocked_child_still_stops_the_subtree(db_session, workspace):
    """AC4. Parking is a new option, not a reinterpretation of `blocked`."""
    assert derive_parent_state([TicketState.BLOCKED, TicketState.DONE]) is TicketState.BLOCKED


def test_a_parent_whose_children_are_parked_is_not_done(db_session, workspace):
    """AC3. `parked` is not a resolution, so it cannot complete a parent."""
    assert derive_parent_state([TicketState.PARKED]) is TicketState.IN_PROGRESS
    assert derive_parent_state([TicketState.PARKED, TicketState.DONE]) is TicketState.IN_PROGRESS
    # And it is not mistaken for the urgent case either.
    assert derive_parent_state([TicketState.PARKED, TicketState.DONE]) is not TicketState.BLOCKED


def test_parking_survives_recomputation(db_session, workspace):
    """AC2. A recomputation over stages or children cannot un-park a ticket —
    only a decision can. Without this, the next rollup silently reopens it."""
    ticket = _ticket(db_session, workspace, title="owed", state=TicketState.PARKED)
    assert derive(ticket, TicketState.IN_PROGRESS, actor="test") is False
    assert ticket.state is TicketState.PARKED
    # A person can still move it, because that is a decision rather than a tally.
    assert can_choose(TicketState.PARKED, TicketState.IN_PROGRESS)
    assert can_choose(TicketState.BLOCKED, TicketState.PARKED)


def test_a_ticket_can_be_lifted_off_a_critical_path(db_session, workspace):
    """AC5. Reparenting, the other half of "carry on without it"."""
    milestone = _ticket(db_session, workspace, title="m", work_item_type=WorkItemType.MILESTONE)
    feature = _ticket(
        db_session,
        workspace,
        title="f",
        work_item_type=WorkItemType.FEATURE,
        parent_id=milestone.id,
    )
    critical_path = _ticket(
        db_session,
        workspace,
        title="ca",
        work_item_type=WorkItemType.CAPABILITY,
        parent_id=feature.id,
    )
    somewhere_else = _ticket(
        db_session,
        workspace,
        title="cb",
        work_item_type=WorkItemType.CAPABILITY,
        parent_id=feature.id,
    )
    task = _ticket(db_session, workspace, title="t", parent_id=critical_path.id)

    reparent_ticket(db_session, task, somewhere_else.id)
    db_session.commit()
    assert task.parent_ticket_id == somewhere_else.id


def test_reparenting_refuses_to_build_a_cycle(db_session, workspace):
    """The one rule creation does not need: only a move can put a work item
    underneath itself."""
    milestone = _ticket(db_session, workspace, title="m2", work_item_type=WorkItemType.MILESTONE)
    feature = _ticket(
        db_session,
        workspace,
        title="f2",
        work_item_type=WorkItemType.FEATURE,
        parent_id=milestone.id,
    )
    capability = _ticket(
        db_session,
        workspace,
        title="c2",
        work_item_type=WorkItemType.CAPABILITY,
        parent_id=feature.id,
    )

    with pytest.raises(ValueError, match="descendants"):
        reparent_ticket(db_session, feature, capability.id)


def _child_ticket(client, title: str, work_item_type: str = "bug") -> dict:
    milestone_id = next(
        t["id"]
        for t in client.get("/api/tickets?workspace=loregarden").json()
        if t["work_item_type"] == "milestone"
    )
    res = client.post(
        "/api/tickets",
        json={
            "workspace_slug": "loregarden",
            "title": title,
            "work_item_type": work_item_type,
            "parent_ticket_id": milestone_id,
            "description": "Parking fixture.",
        },
    )
    assert res.status_code == 201, res.text
    return res.json()


def test_parking_and_reparenting_work_through_mcp(client):
    """AC5, and the reason it is an end-to-end test rather than a unit one.

    `reparent_ticket` being correct says nothing about whether the MCP tool can
    reach it: the argument has to survive the normalizer whitelist, which drops
    anything not listed, and the handler has to resolve an external id. Both are
    places a change passes ruff and mypy while doing nothing at all.
    """
    ticket = _child_ticket(client, "Needs a person")
    other = _child_ticket(client, "Somewhere else", work_item_type="feature")

    body = call_mcp(
        client, "loregarden_update_ticket", {"ticket_id": ticket["id"], "state": "parked"}
    )
    assert "error" not in body, body
    assert client.get(f"/api/tickets/{ticket['id']}").json()["state"] == "parked"

    body = call_mcp(
        client, "loregarden_update_ticket", {"ticket_id": ticket["id"], "parent": other["id"]}
    )
    assert "error" not in body, body
    assert client.get(f"/api/tickets/{ticket['id']}").json()["parent_ticket_id"] == other["id"]


def test_parking_and_reparenting_work_through_rest(client):
    """AC5's other half. PATCH forbids unknown fields, so a body this model does
    not declare is rejected rather than silently dropped — which is the failure
    `parent_ticket_id` would otherwise repeat."""
    ticket = _child_ticket(client, "REST parking target")
    other = _child_ticket(client, "REST destination", work_item_type="feature")

    res = client.patch(f"/api/tickets/{ticket['id']}", json={"state": "parked"})
    assert res.status_code == 200, res.text
    assert res.json()["state"] == "parked"

    res = client.patch(f"/api/tickets/{ticket['id']}", json={"parent_ticket_id": other["id"]})
    assert res.status_code == 200, res.text
    assert res.json()["parent_ticket_id"] == other["id"]


def test_rest_refuses_a_reparent_that_would_build_a_cycle(client):
    """A bad move must answer 400, not 500 — `update_ticket_manual` turns the
    ValueError into an HTTP error only if it is raised where it can catch it."""
    parent = _child_ticket(client, "Cycle parent")
    res = client.patch(f"/api/tickets/{parent['id']}", json={"parent_ticket_id": parent["id"]})
    assert res.status_code == 400, res.text
    assert "own parent" in res.text
