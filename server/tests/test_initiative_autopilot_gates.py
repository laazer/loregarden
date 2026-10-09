"""The autopilot and a ticket parked on a workflow gate.

A gate parks a stage with no live run, so the ticket used to read idle and the
next tick queued it again — `lg-durable-remote-336` was queued a second time one
second after its Break Tests gate opened. A parked ticket holds its lane; the
tick answers the gates that are its to answer and leaves the rest to a person.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

from loregarden.models.domain import (
    Approval,
    ApprovalKind,
    ApprovalStatus,
    AutopilotAction,
    AutopilotEvent,
    AutopilotUpdate,
    NodeStatus,
    OrchestrationRun,
    OrchestrationRunStatus,
    StageStatus,
    Ticket,
)
from loregarden.services.initiative_autopilot import run_autopilot, set_autopilot
from loregarden.services.initiative_plan_service import plan_view
from sqlmodel import Session, select
from tests.test_initiative_graph import (  # noqa: F401 - fixtures are used by name
    NOW,
    _agent_time,
    _initiative,
    _item,
    _milestone,
    _queued,
    queue_fixture,
    workspaces_fixture,
)


def _gate(session: Session, ticket: Ticket, action_key: str) -> Approval:
    """Park `ticket` on a pending operator-judgment gate whose one action is `action_key`."""
    ticket.workflow_stage_key = "test-break"
    ticket.workflow_stage_status = StageStatus.AWAITING
    approval = Approval(
        ticket_id=ticket.id,
        workspace_id=ticket.workspace_id,
        kind=ApprovalKind.WORKFLOW_GATE,
        stage_key="test-break",
        status=ApprovalStatus.PENDING,
        title="Resolve Break Tests exit actions",
        tool_input_json=json.dumps(
            {
                "human_required_actions": [
                    {
                        "action_key": action_key,
                        "action_label": "Approve",
                        "requirement": {"kind": "operator_judgment", "decision_prompt": "Ok?"},
                        "reason_code": "operator_judgment_required",
                        "reason": "Operator judgment required.",
                        "resolution_mode": "approve",
                    }
                ],
                "allowed_actions": ["approve", "reject"],
            }
        ),
    )
    session.add_all([ticket, approval])
    session.commit()
    return approval


def _started_by_autopilot(session: Session, initiative: Ticket, ticket: Ticket) -> None:
    session.add(
        AutopilotEvent(
            initiative_id=initiative.id, action=AutopilotAction.DISPATCHED, ticket_id=ticket.id
        )
    )
    session.commit()


def _approving_service(session: Session) -> MagicMock:
    """ApprovalService whose resolve approves the row; the real one also resumes the workflow."""
    service = MagicMock()

    def resolve(approval_id: str, *, approved: bool) -> None:
        row = session.get(Approval, approval_id)
        assert row is not None
        row.status = ApprovalStatus.APPROVED if approved else ApprovalStatus.REJECTED
        # As the real resolve does: the gate's stage is passed, so the ticket
        # no longer reads parked while its resume starts.
        ticket = session.get(Ticket, row.ticket_id)
        assert ticket is not None
        ticket.workflow_stage_status = StageStatus.PENDING
        session.add_all([row, ticket])
        session.commit()

    service.resolve.side_effect = resolve
    return service


def test_a_gated_ticket_holds_its_lane_and_is_not_queued_again(db_session, workspaces, queue):
    lanes, _ = queue
    here, _ = workspaces
    initiative = _initiative(db_session)
    m = _milestone(db_session, here, "M", initiative)
    parked = _item(db_session, here, m, "parked", lane="a")
    _gate(db_session, parked, "ship-to-users")  # a real question: the tick leaves it alone
    _item(db_session, here, m, "same-lane", lane="a")
    set_autopilot(db_session, initiative.id, AutopilotUpdate(enabled=True), actor="t")

    run_autopilot(db_session, initiative.id, now=NOW)

    assert _queued(lanes) == []  # not the parked ticket, and not behind it in its lane
    node = next(n for n in plan_view(db_session, initiative.id, now=NOW).nodes if n.id == parked.id)
    assert node.status == NodeStatus.NEEDS_PERSON


def test_the_tick_answers_a_parked_legacy_gate_on_work_it_started(db_session, workspaces, queue):
    lanes, _ = queue
    here, _ = workspaces
    initiative = _initiative(db_session)
    m = _milestone(db_session, here, "M", initiative)
    parked = _item(db_session, here, m, "parked", lane="a")
    _started_by_autopilot(db_session, initiative, parked)
    gate = _gate(db_session, parked, "legacy-stage-sign-off")
    set_autopilot(db_session, initiative.id, AutopilotUpdate(enabled=True), actor="t")
    service = _approving_service(db_session)

    with patch("loregarden.services.initiative_autopilot.ApprovalService", return_value=service):
        run_autopilot(db_session, initiative.id, now=NOW)

    db_session.refresh(gate)
    assert gate.status is ApprovalStatus.APPROVED
    assert gate.resolved_by == "autopilot"
    signed = db_session.exec(
        select(AutopilotEvent).where(AutopilotEvent.action == AutopilotAction.SIGNED_OFF)
    ).all()
    assert [e.ticket_id for e in signed] == [parked.id]
    # Approving resumes it on another thread; this tick must not queue it as well.
    assert parked.id not in _queued(lanes)


def test_the_tick_leaves_gates_on_work_it_did_not_start(db_session, workspaces, queue):
    here, _ = workspaces
    initiative = _initiative(db_session)
    m = _milestone(db_session, here, "M", initiative)
    parked = _item(db_session, here, m, "parked", lane="a")
    gate = _gate(db_session, parked, "legacy-stage-sign-off")
    set_autopilot(db_session, initiative.id, AutopilotUpdate(enabled=True), actor="t")
    service = _approving_service(db_session)

    with patch("loregarden.services.initiative_autopilot.ApprovalService", return_value=service):
        run_autopilot(db_session, initiative.id, now=NOW)

    db_session.refresh(gate)
    assert gate.status is ApprovalStatus.PENDING
    service.resolve.assert_not_called()


def test_a_refused_sign_off_is_logged_once_not_every_tick(db_session, workspaces, queue):
    here, _ = workspaces
    initiative = _initiative(db_session)
    m = _milestone(db_session, here, "M", initiative)
    parked = _item(db_session, here, m, "parked", lane="a")
    _started_by_autopilot(db_session, initiative, parked)
    gate = _gate(db_session, parked, "legacy-stage-sign-off")
    set_autopilot(db_session, initiative.id, AutopilotUpdate(enabled=True), actor="t")
    service = MagicMock()
    service.resolve.side_effect = ValueError("this gate would skip work nobody did")

    with patch("loregarden.services.initiative_autopilot.ApprovalService", return_value=service):
        run_autopilot(db_session, initiative.id, now=NOW)
        run_autopilot(db_session, initiative.id, now=NOW)

    db_session.refresh(gate)
    assert gate.status is ApprovalStatus.PENDING
    assert service.resolve.call_count == 2  # it tries each tick
    refused = db_session.exec(
        select(AutopilotEvent).where(
            AutopilotEvent.ticket_id == parked.id,
            AutopilotEvent.action == AutopilotAction.REFUSED,
        )
    ).all()
    assert len(refused) == 1  # and says so once


def test_a_gate_under_a_live_orchestration_is_left_to_it(db_session, workspaces, queue):
    """The orchestration's own loop resolves its gate; a second resolve races it."""
    here, _ = workspaces
    initiative = _initiative(db_session)
    m = _milestone(db_session, here, "M", initiative)
    parked = _item(db_session, here, m, "parked", lane="a")
    _started_by_autopilot(db_session, initiative, parked)
    gate = _gate(db_session, parked, "legacy-stage-sign-off")
    db_session.add(
        OrchestrationRun(
            run_code="orch_live",
            ticket_id=parked.id,
            workspace_id=parked.workspace_id,
            status=OrchestrationRunStatus.RUNNING,
        )
    )
    db_session.commit()
    set_autopilot(db_session, initiative.id, AutopilotUpdate(enabled=True), actor="t")
    service = _approving_service(db_session)

    with patch("loregarden.services.initiative_autopilot.ApprovalService", return_value=service):
        run_autopilot(db_session, initiative.id, now=NOW)

    service.resolve.assert_not_called()
    db_session.refresh(gate)
    assert gate.status is ApprovalStatus.PENDING


def test_a_parent_whose_child_is_parked_is_not_queued(db_session, workspaces, queue):
    """Its orchestration would run the parked child a second time."""
    lanes, _ = queue
    here, _ = workspaces
    initiative = _initiative(db_session)
    m = _milestone(db_session, here, "M", initiative)
    parent = _item(db_session, here, m, "parent", lane="a")
    child = _item(db_session, here, m, "child", lane="b")
    child.parent_ticket_id = parent.id
    db_session.add(child)
    db_session.commit()
    _gate(db_session, child, "ship-to-users")
    set_autopilot(db_session, initiative.id, AutopilotUpdate(enabled=True), actor="t")

    run_autopilot(db_session, initiative.id, now=NOW)

    assert parent.id not in _queued(lanes)
    nodes = {n.id: n for n in plan_view(db_session, initiative.id, now=NOW).nodes}
    assert nodes[parent.id].status == NodeStatus.NEEDS_PERSON


def test_the_tick_answers_a_child_gate_and_holds_back_its_parent(db_session, workspaces, queue):
    lanes, _ = queue
    here, _ = workspaces
    initiative = _initiative(db_session)
    m = _milestone(db_session, here, "M", initiative)
    parent = _item(db_session, here, m, "parent", lane="a")
    child = _item(db_session, here, m, "child", lane="b")
    child.parent_ticket_id = parent.id
    db_session.add(child)
    db_session.commit()
    _started_by_autopilot(db_session, initiative, parent)
    gate = _gate(db_session, child, "legacy-stage-sign-off")
    set_autopilot(db_session, initiative.id, AutopilotUpdate(enabled=True), actor="t")
    service = _approving_service(db_session)

    with patch("loregarden.services.initiative_autopilot.ApprovalService", return_value=service):
        run_autopilot(db_session, initiative.id, now=NOW)

    db_session.refresh(gate)
    assert gate.status is ApprovalStatus.APPROVED
    # The child resumes on its own; neither it nor its parent is queued as well.
    assert {parent.id, child.id}.isdisjoint(_queued(lanes))
